"""Meterless World Model: Python implementation of the Meterless reference
architecture (github.com/Meterless/Meterless, engines/world-model, Apache-2.0,
commit 1742dec, 19 Oct 2026). Breach72 uses it as the shared case state.

Ported from the TypeScript reference (stableIds.ts, store.ts, index.ts):
- stable, content-addressed ids (sha-256, canonical JSON, the `type:system:id`
  grammar for externally keyed records);
- "no provenance, no write": every write carries {kind, at}, rejected otherwise;
- event-sourced store: state is a fold over an append-only event log, persisted
  as one JSON file per namespace (<directory>/.world/<namespace>.json);
- the write primitives upsertEntity, upsertContext, relate, assertFact;
- assertFact conflict policy "most-recent-wins": a newer fact on the same
  (about, predicate) supersedes the older one, which is kept, never deleted;
- an audit entry for every mutation; foldUntil(at) replays the log to a time.

Not ported (not needed by Breach72): name-similarity merge resolver, the
stream-ingest clustering pipeline, scoring views, control plane UI.

ContextBuilder renders the current facts of a case, with their provenance,
for the agents and for model prompts.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
import unicodedata
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------- stable ids
def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def canonical_json(value: Any) -> str:
    """Deterministic JSON: keys sorted, None members of objects dropped."""
    if isinstance(value, dict):
        keys = sorted(k for k, v in value.items() if v is not None)
        return "{" + ",".join(json.dumps(k) + ":" + canonical_json(value[k]) for k in keys) + "}"
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(canonical_json(v) for v in value) + "]"
    return json.dumps(value, ensure_ascii=False, default=str)


_ZERO_WIDTH = re.compile("[​-‍﻿]")


def normalize_name(name: str) -> str:
    s = unicodedata.normalize("NFKD", name)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = _ZERO_WIDTH.sub("", s).lower()
    return re.sub(r"\s+", " ", s).strip()


def external_id(type_: str, system: str, id_: str) -> str:
    return f"{type_}:{system}:{id_}"


def entity_id_from_name(type_: str, name: str) -> str:
    return "ent_" + sha256_hex(f"{type_}:{normalize_name(name)}")[:16]


def relationship_id(frm: str, type_: str, to: str, context: str | None = None,
                    valid_from: str | None = None) -> str:
    return "rel_" + sha256_hex(f"{frm}|{type_}|{to}|{context or ''}|{valid_from or ''}")[:16]


def fact_id(about: str, predicate: str, value: Any, asserted_at: str, source_kind: str,
            source_url: str | None = None) -> str:
    return "fact_" + sha256_hex(
        f"{about}|{predicate}|{canonical_json(value)}|{asserted_at}|{source_kind}|{source_url or ''}")[:16]


class ProvenanceError(ValueError):
    pass


# ---------------------------------------------------------------- the model
class WorldModel:
    def __init__(self, namespace: str, storage: str = "memory", directory: Path | None = None):
        if not namespace:
            raise ValueError("WorldModel: namespace is required")
        self.namespace = namespace
        self.events: list[dict] = []
        self.audit: list[dict] = []
        self.entities: dict[str, dict] = {}
        self.contexts: dict[str, dict] = {}
        self.relationships: dict[str, dict] = {}
        self.facts: dict[str, dict] = {}
        self._seq = 0
        self.file: Path | None = None
        if storage == "local":
            d = Path(directory or ".") / ".world"
            d.mkdir(parents=True, exist_ok=True)
            self.file = d / f"{namespace}.json"
            if self.file.exists():
                saved = json.loads(self.file.read_text(encoding="utf-8"))
                self.audit = saved.get("audit", [])
                for e in saved.get("events", []):
                    self._apply(e, new=False)

    # ---- event log ----------------------------------------------------
    def _append(self, kind: str, payload: dict, at: str) -> dict:
        event = {"id": f"evt_{self._seq + 1}", "seq": self._seq + 1, "kind": kind, "payload": payload,
                 "schemaVersion": 1, "at": at}
        self._apply(event, new=True)
        return event

    def _apply(self, event: dict, new: bool) -> None:
        self._seq = max(self._seq, event["seq"])
        self.events.append(event)
        self._fold(event, self)
        if new:
            self.persist()

    @staticmethod
    def _fold(event: dict, st: "WorldModel") -> None:
        kind, p = event["kind"], event["payload"]
        if kind == "upsertEntity":
            cur = st.entities.get(p["id"])
            if cur:
                cur["attrs"].update(p["attrs"])
                cur["updatedAt"], cur["source"] = p["updatedAt"], p["source"]
                if p.get("name"):
                    cur["name"] = p["name"]
                    if p["name"] not in cur["aliases"]:
                        cur["aliases"].append(p["name"])
            else:
                st.entities[p["id"]] = copy.deepcopy(p)
        elif kind == "upsertContext":
            cur = st.contexts.get(p["id"])
            if cur:
                cur["attrs"].update(p["attrs"])
                cur["updatedAt"] = p["updatedAt"]
            else:
                st.contexts[p["id"]] = copy.deepcopy(p)
        elif kind == "relate":
            for other in st.relationships.values():
                if (other["from"] == p["from"] and other["type"] == p["type"]
                        and (other.get("context") or "") == (p.get("context") or "")
                        and other["to"] != p["to"] and not other.get("validTo") and not other.get("supersededBy")):
                    other["validTo"] = p.get("validFrom") or p["createdAt"]
                    other["supersededBy"] = p["id"]
            st.relationships.setdefault(p["id"], copy.deepcopy(p))
        elif kind == "assertFact":
            if p["id"] not in st.facts:
                st.facts[p["id"]] = copy.deepcopy(p)
            # most-recent-wins, replayed deterministically from the log
            rivals = [f for f in st.facts.values() if f["about"] == p["about"] and f["predicate"] == p["predicate"]
                      and f["id"] != p["id"] and not f.get("supersededBy")]
            winner = st.facts[p["id"]]
            for r in rivals:
                if r["assertedAt"] > winner["assertedAt"]:
                    winner = r
            for f in [st.facts[p["id"]], *rivals]:
                if f["id"] != winner["id"]:
                    f["supersededBy"] = winner["id"]
            ent = st.entities.get(p["about"])
            if ent is None:
                ent = {"type": p["about"].split(":")[0] if ":" in p["about"] else "entity", "id": p["about"],
                       "attrs": {}, "schemaVersion": 1, "source": p["source"], "createdAt": p["assertedAt"],
                       "updatedAt": p["assertedAt"], "aliases": [], "state": "emerging"}
                st.entities[p["about"]] = ent
            ent["attrs"][p["predicate"]] = winner["value"]
            ent["updatedAt"] = p["assertedAt"]

    def _log(self, kind: str, actor: str, summary: str, event_id: str, run_id: str | None, at: str) -> None:
        self.audit.append({"seq": len(self.audit) + 1, "at": at, "kind": kind, "actor": actor, "summary": summary,
                           "eventId": event_id, "runId": run_id})
        self.persist()

    def persist(self) -> None:
        if self.file:
            self.file.write_text(json.dumps({"events": self.events, "audit": self.audit}, indent=1,
                                            ensure_ascii=False, default=str) + "\n", encoding="utf-8")

    @staticmethod
    def _prov(source: dict | None) -> dict:
        if not source or not source.get("kind") or not source.get("at"):
            raise ProvenanceError("World Model: every write carries provenance (kind + at). No provenance, no write.")
        return {k: v for k, v in source.items() if v is not None}

    # ---- write primitives ------------------------------------------------
    def upsert_entity(self, type_: str, source: dict, external_key: tuple[str, str] | None = None,
                      name: str | None = None, attrs: dict | None = None, state: str = "active") -> dict:
        src = self._prov(source)
        if external_key:
            eid = external_id(type_, *external_key)
        elif name:
            eid = entity_id_from_name(type_, name)
        else:
            raise ValueError("World Model: entity needs an external key or a name")
        ent = {"type": type_, "id": eid, "attrs": dict(attrs or {}), "schemaVersion": 1, "source": src,
               "createdAt": src["at"], "updatedAt": src["at"], "name": name, "aliases": [name] if name else [],
               "state": state}
        ev = self._append("upsertEntity", ent, src["at"])
        self._log("upsertEntity", src.get("by", "system"), f"{eid} ({type_})", ev["id"], src.get("runId"), src["at"])
        return self.entities[eid]

    def upsert_context(self, type_: str, source: dict, external_key: tuple[str, str],
                       attrs: dict | None = None, parent: str | None = None) -> dict:
        src = self._prov(source)
        cid = external_id(type_, *external_key)
        ctx = {"type": type_, "id": cid, "attrs": dict(attrs or {}), "parent": parent, "schemaVersion": 1,
               "source": src, "createdAt": src["at"], "updatedAt": src["at"]}
        ev = self._append("upsertContext", ctx, src["at"])
        self._log("upsertContext", src.get("by", "system"), f"{cid} ({type_})", ev["id"], src.get("runId"), src["at"])
        return self.contexts[cid]

    def relate(self, frm: str, type_: str, to: str, source: dict, context: str | None = None,
               attrs: dict | None = None) -> dict:
        src = self._prov(source)
        rid = relationship_id(frm, type_, to, context, None)
        if rid in self.relationships:
            return self.relationships[rid]          # content-addressed no-op
        rel = {"id": rid, "from": frm, "type": type_, "to": to, "context": context, "attrs": attrs,
               "validFrom": src["at"], "schemaVersion": 1, "source": src, "createdAt": src["at"]}
        ev = self._append("relate", rel, src["at"])
        self._log("relate", src.get("by", "system"), f"{frm} -[{type_}]-> {to}", ev["id"], src.get("runId"),
                  src["at"])
        return self.relationships[rid]

    def assert_fact(self, about: str, predicate: str, value: Any, source: dict, confidence: float = 0.5,
                    context: str | None = None) -> dict:
        src = self._prov(source)
        fid = fact_id(about, predicate, value, src["at"], src["kind"], src.get("url"))
        if fid in self.facts:
            return self.facts[fid]                   # identical fact: no-op
        fact = {"id": fid, "about": about, "predicate": predicate, "value": copy.deepcopy(value),
                "confidence": confidence, "context": context, "source": src, "assertedAt": src["at"]}
        ev = self._append("assertFact", fact, src["at"])
        self._log("assertFact", src.get("by", "system"), f"{about}.{predicate} (most-recent-wins)", ev["id"],
                  src.get("runId"), src["at"])
        return self.facts[fid]

    # ---- reads -------------------------------------------------------------
    def current(self, about: str, predicate: str) -> dict | None:
        live = [f for f in self.facts.values() if f["about"] == about and f["predicate"] == predicate
                and not f.get("supersededBy")]
        return live[0] if live else None

    def history(self, about: str, predicate: str) -> list[dict]:
        """All facts for (about, predicate), oldest first, superseded ones included."""
        return sorted((f for f in self.facts.values() if f["about"] == about and f["predicate"] == predicate),
                      key=lambda f: (f["assertedAt"], f["id"]))

    def current_facts(self, about: str) -> list[dict]:
        return sorted((f for f in self.facts.values() if f["about"] == about and not f.get("supersededBy")),
                      key=lambda f: f["predicate"])

    def neighbors(self, entity_id: str, via: str | None = None) -> list[dict]:
        out = []
        for r in self.relationships.values():
            if r.get("validTo") or (via and r["type"] != via):
                continue
            other = r["to"] if r["from"] == entity_id else r["from"] if r["to"] == entity_id else None
            if other and other in self.entities:
                out.append(self.entities[other])
        return sorted(out, key=lambda e: e["id"])

    def fold_until(self, at: str) -> "WorldModel":
        scratch = WorldModel("scratch")
        for e in self.events:
            if e["at"] <= at:
                WorldModel._fold(e, scratch)
        return scratch

    def stats(self) -> dict:
        return {"namespace": self.namespace, "events": len(self.events), "entities": len(self.entities),
                "relationships": len(self.relationships), "facts": len(self.facts),
                "facts_superseded": sum(1 for f in self.facts.values() if f.get("supersededBy")),
                "audit_entries": len(self.audit)}


class ContextBuilder:
    """Renders the current state of a case for agents and model prompts.
    Every line carries the fact id and the source it came from."""

    def __init__(self, world: WorldModel):
        self.world = world

    def build(self, about: str, max_neighbors: int = 10) -> dict:
        ent = self.world.entities.get(about)
        facts = self.world.current_facts(about)
        lines = [f"[WORLD MODEL CONTEXT {about}]"]
        for f in facts:
            v = f["value"]
            stmt = v.get("statement", "") if isinstance(v, dict) else str(v)
            status = v.get("status", "") if isinstance(v, dict) else ""
            fid = f"{v['finding_id']} " if isinstance(v, dict) and v.get("finding_id") else ""
            src = f["source"]
            lines.append(f"- {fid}{f['predicate']} [{status}] {stmt} <{f['id']}; {src.get('by', '')}; "
                         f"{src.get('url', src.get('kind'))}>")
        for n in self.world.neighbors(about)[:max_neighbors]:
            lines.append(f"- related: {n['id']} ({n['type']})")
        lines.append("[/WORLD MODEL CONTEXT]")
        return {"entity": ent, "facts": facts, "text": "\n".join(lines)}
