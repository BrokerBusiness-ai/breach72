"""Meterless H-MEM: Python implementation of the Meterless reference architecture
(github.com/Meterless/Meterless, engines/hmem, Apache-2.0, commit 1742dec,
19 Oct 2026). Breach72 uses it as organisational memory across cases.

Ported from the TypeScript reference (types.ts, embeddings.ts, memoryStore.ts,
memoryRetrieval.ts, trustLedger.ts):
- MemoryStoreService: tiered records (short_term / working / long_term); a record
  without a source is rejected by construction; enrichment on add (domain,
  namespace, entities cap 10, relatedTo cap 5, bidirectional);
- MemoryRetrievalService: the canonical hybrid score
      raw = 0.35*semantic + 0.20*keyword + 0.10*tag + 0.10*domain
          + 0.15*entity + 0.05*layer + 0.05*recency - 0.20*superseded
      score = clamp01(raw) * confidence
  threshold 0.35 on the final score, top N = 5, superseded records penalised,
  never deleted; feedback deltas helpful +0.05, not_helpful -0.03, wrong -0.20;
- TrustLedgerService: append-only ledger.jsonl of every mutation and read;
- the deterministic FNV-1a 64-dim embedder of the reference (no model call).

Not ported (not needed by Breach72): sleep cycle, dreaming, memory mining,
conflict detection service. Breach72 handles conflicts itself: a recalled
memory is always re-verified at the source before it is used.
"""
from __future__ import annotations

import copy
import json
import math
import re
from pathlib import Path

DEFAULTS = {"retrievalTopN": 5, "retrievalThreshold": 0.35, "entityCap": 10, "relatedCap": 5, "recencyDays": 14}
FEEDBACK_DELTAS = {"helpful": 0.05, "not_helpful": -0.03, "wrong": -0.2}
DAY_MS = 24 * 60 * 60 * 1000
LAYER_WEIGHT = {"long_term": 1.0, "working": 0.8, "short_term": 0.6}
DIM = 64


def clamp01(x: float) -> float:
    return min(1.0, max(0.0, x))


# ---------------------------------------------------------------- embeddings
def tokenize(text: str) -> list[str]:
    return [t for t in re.sub(r"[^a-z0-9\s]", " ", text.lower()).split() if len(t) > 1]


def _fnv1a(s: str) -> int:
    h = 0x811C9DC5
    for ch in s:
        h ^= ord(ch)
        h = (h * 0x01000193) & 0xFFFFFFFF
    return h


def embed(text: str) -> list[float]:
    vec = [0.0] * DIM
    for tok in tokenize(text):
        h = _fnv1a(tok)
        sign = 1 if (h >> 8) % 2 == 0 else -1
        vec[h % DIM] += sign * (1 + ((h >> 16) % 8) / 8)
    norm = math.sqrt(sum(v * v for v in vec))
    return vec if norm == 0 else [v / norm for v in vec]


def cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def jaccard(a, b) -> float:
    sa, sb = set(a), set(b)
    if not sa or not sb:
        return 0.0
    inter = len(sa & sb)
    return inter / (len(sa) + len(sb) - inter)


def snapshot(rec: dict) -> dict:
    out = copy.deepcopy(rec)
    out.pop("embedding", None)
    return out


# ---------------------------------------------------------------- ledger
class TrustLedgerService:
    def __init__(self, clock, persist_dir: Path | None = None):
        self.clock, self.entries, self.file = clock, [], None
        if persist_dir:
            self.file = Path(persist_dir) / "ledger.jsonl"
            if self.file.exists():
                self.entries = [json.loads(x) for x in self.file.read_text(encoding="utf-8").splitlines() if x]

    def log(self, memory_id: str, action: str, actor: str, **extra) -> dict:
        entry = {"memoryId": memory_id, "action": action, "actor": actor, "timestamp": self.clock(),
                 **{k: v for k, v in extra.items() if v is not None}}
        self.entries.append(entry)
        if self.file:
            with open(self.file, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
        return entry

    def history(self, memory_id: str) -> list[dict]:
        return [e for e in self.entries if e["memoryId"] == memory_id]

    def stats(self) -> dict:
        out: dict[str, int] = {}
        for e in self.entries:
            out[e["action"]] = out.get(e["action"], 0) + 1
        return out


# ---------------------------------------------------------------- store
DOMAIN_KEYWORDS = {
    "gdpr": ["encryption", "backup", "processor", "contract", "processing", "ropa", "asset", "breach", "subjects",
             "categories", "pesel", "notice"],
    "tech": ["server", "database", "laptop", "saas", "web", "api"],
}
_STOP = {"the", "this", "that", "with"}


class MemoryStoreService:
    def __init__(self, clock, ledger: TrustLedgerService, actor: str, persist_dir: Path | None = None):
        self.clock, self.ledger, self.actor = clock, ledger, actor
        self.records: dict[str, dict] = {}
        self.seq, self.state_file = 0, None
        if persist_dir:
            Path(persist_dir).mkdir(parents=True, exist_ok=True)
            self.state_file = Path(persist_dir) / "state.json"
            if self.state_file.exists():
                saved = json.loads(self.state_file.read_text(encoding="utf-8"))
                self.seq = saved["seq"]
                self.records = {r["id"]: r for r in saved["records"]}

    def persist(self) -> None:
        if self.state_file:
            recs = sorted(self.records.values(), key=lambda r: int(r["id"].split("-")[1]))
            self.state_file.write_text(json.dumps({"seq": self.seq, "records": recs}, indent=1, ensure_ascii=False)
                                       + "\n", encoding="utf-8")

    def add(self, content: str, source: str, type_: str = "general", layer: str = "working",
            tags: list[str] | None = None, confidence: float = 0.7, provenance: dict | None = None,
            supersedes: str | None = None, derived_from: list[str] | None = None) -> dict:
        if not source or not source.strip():
            raise ValueError("H-MEM: a memory record with no source must be rejected by construction")
        now = self.clock()
        self.seq += 1
        tags = list(tags or [])
        domain = self.classify_domain(content, tags)
        rec = {"id": f"mem-{self.seq}", "content": content, "type": type_, "layer": layer, "timestamp": now,
               "lastAccessed": now, "accessCount": 0, "tags": tags, "embedding": embed(content),
               "confidence": clamp01(confidence), "source": source,
               "provenance": provenance or {"origin": "manual", "learnedAt": now, "label": source},
               "domain": domain, "namespace": f"{domain}/{'facts' if type_ == 'factual' else 'notes'}",
               "entities": self.extract_entities(content), "relatedTo": [], "supersedes": supersedes,
               "supersededBy": None, "derivedFrom": derived_from}
        self._discover_relationships(rec)
        self.records[rec["id"]] = rec
        self.ledger.log(rec["id"], "create", self.actor, newState=snapshot(rec))
        self.persist()
        return rec

    def get(self, mid: str) -> dict | None:
        return self.records.get(mid)

    def all(self) -> list[dict]:
        return list(self.records.values())

    def update(self, mid: str, patch: dict, action: str = "update", details: dict | None = None) -> dict:
        rec = self.records[mid]
        prev = snapshot(rec)
        rec.update(patch)
        self.ledger.log(mid, action, self.actor, previousState=prev, newState=snapshot(rec), details=details)
        self.persist()
        return rec

    def touch(self, mid: str) -> None:
        rec = self.records[mid]
        rec["lastAccessed"] = self.clock()
        rec["accessCount"] += 1

    def promote(self, mid: str, layer: str) -> None:
        if self.records[mid]["layer"] != layer:
            self.update(mid, {"layer": layer}, action="promote")

    def classify_domain(self, content: str, tags: list[str]) -> str:
        for t in tags:
            if t.startswith("project:"):
                return t[len("project:"):]
        toks = set(tokenize(content))
        best, best_score = "general", 0
        for dom, kws in DOMAIN_KEYWORDS.items():
            sc = sum(1 for k in kws if k in toks)
            if sc > best_score:
                best, best_score = dom, sc
        return best

    def extract_entities(self, content: str) -> list[str]:
        ents: list[str] = []
        for m in re.finditer(r"\b([A-Z][A-Za-z0-9]{2,}(?:-[A-Z0-9]+)*)\b", content):
            n = m.group(1).lower()
            if n not in _STOP and n not in ents:
                ents.append(n)
        return ents[:DEFAULTS["entityCap"]]

    def _discover_relationships(self, rec: dict) -> None:
        for other in self.records.values():
            if len(rec["relatedTo"]) >= DEFAULTS["relatedCap"]:
                break
            if set(other["entities"]) & set(rec["entities"]) and other["domain"] == rec["domain"]:
                rec["relatedTo"].append(other["id"])
                if len(other["relatedTo"]) < DEFAULTS["relatedCap"] and rec["id"] not in other["relatedTo"]:
                    other["relatedTo"].append(rec["id"])


# ---------------------------------------------------------------- retrieval
class MemoryRetrievalService:
    def __init__(self, store: MemoryStoreService, ledger: TrustLedgerService, clock, actor: str):
        self.store, self.ledger, self.clock, self.actor = store, ledger, clock, actor

    def score(self, m: dict, q_emb, q_tokens, q_entities, q_domain) -> float:
        semantic = clamp01(cosine(q_emb, m["embedding"]))
        keyword = jaccard(q_tokens, tokenize(m["content"]))
        tag = jaccard(q_tokens, [t.lower() for t in m["tags"]])
        domain = 1.0 if m["domain"] == q_domain else 0.0
        entity = jaccard(q_entities, m["entities"])
        layer = LAYER_WEIGHT[m["layer"]]
        recency = math.exp(-((self.clock() - m["lastAccessed"]) / DAY_MS) / DEFAULTS["recencyDays"])
        superseded = 1.0 if m.get("supersededBy") else 0.0
        raw = (0.35 * semantic + 0.2 * keyword + 0.1 * tag + 0.1 * domain + 0.15 * entity + 0.05 * layer
               + 0.05 * recency - 0.2 * superseded)
        return clamp01(raw) * m["confidence"]

    def query(self, query: str, top_n: int | None = None, threshold: float | None = None,
              require_tags: list[str] | None = None) -> dict:
        q_emb, q_tokens = embed(query), tokenize(query)[:8]
        q_entities = self.store.extract_entities(query)
        q_domain = self.store.classify_domain(query, [])
        th = DEFAULTS["retrievalThreshold"] if threshold is None else threshold
        scored = []
        for m in self.store.all():
            if require_tags and not set(require_tags) <= set(m["tags"]):
                continue
            s = self.score(m, q_emb, q_tokens, q_entities, q_domain)
            if s >= th:
                scored.append((s, m))
        scored.sort(key=lambda x: (-x[0], x[1]["id"]))
        top = scored[: top_n or DEFAULTS["retrievalTopN"]]
        for _, m in top:
            self.store.touch(m["id"])
            self.ledger.log(m["id"], "read", self.actor, details={"query": query})
        self.store.persist()
        return {"memories": [{"memory": m, "relevance": round(s, 4)} for s, m in top],
                "trace": {"retrievalReason": f'hybrid ranking for "{query}" (domain: {q_domain})',
                          "scores": {m["id"]: round(s, 4) for s, m in top}}}

    def feedback(self, mid: str, kind: str) -> dict:
        m = self.store.records[mid]
        prev = snapshot(m)
        if kind == "helpful":
            m["accessCount"] += 1
        m["confidence"] = clamp01(m["confidence"] + FEEDBACK_DELTAS[kind])
        if kind == "wrong" and "review" not in m["tags"]:
            m["tags"].append("review")
        self.ledger.log(mid, "feedback", self.actor, previousState=prev, newState=snapshot(m),
                        details={"kind": kind, "delta": FEEDBACK_DELTAS[kind]})
        self.store.persist()
        return m


class HMem:
    """Facade wiring the three services, as the reference index.ts does."""

    def __init__(self, persist_dir: Path | None, clock, actor: str = "breach72"):
        self.ledger = TrustLedgerService(clock, persist_dir)
        self.store = MemoryStoreService(clock, self.ledger, actor, persist_dir)
        self.retrieval = MemoryRetrievalService(self.store, self.ledger, clock, actor)
        self.persist_dir = persist_dir
