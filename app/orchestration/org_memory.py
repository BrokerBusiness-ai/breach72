"""Organisational memory across cases (Meterless H-MEM).

learn():  after a case, verified facts about the organisation's systems,
          processing records and processor contracts are stored in H-MEM with
          provenance (run, finding, template, parameters, hash of the rows).
recall(): at the start of a case, H-MEM is queried for facts about the affected
          system (hybrid retrieval, canonical formula).

A recalled fact is never used as evidence on its own: the Evidence Auditor
re-runs the same template at the source (EvidenceAuditor.reverify_memory) and
feeds the outcome back to H-MEM (helpful and promotion to long_term, or wrong
plus a superseding record). Case facts (counts, disclosure) are not memorised.
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime
from pathlib import Path

from app.adapters.meterless_hmem import HMem
from app.adapters.meterless_world import canonical_json

MEMORABLE = {
    "T05_ROPA_FOR_SYSTEM": "Record of processing for system",
    "T06_ASSET_SECURITY": "Asset security register for system",
    "T07_PROCESSOR_CONTRACT": "Processor contract register",
}


def rows_hash(rows: list[dict]) -> str:
    return hashlib.sha256(canonical_json(rows).encode()).hexdigest()[:16]


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text or "default").lower()).strip("-")[:40] or "default"


def clock_from(as_of: str):
    ms = int(datetime.fromisoformat(as_of).timestamp() * 1000)
    return lambda: ms


def open_memory(base: Path, organisation: str, as_of: str) -> HMem:
    return HMem(Path(base) / slug(organisation), clock_from(as_of))


def describe(template_id: str, params: dict, rows: list[dict]) -> str:
    body = " | ".join("; ".join(f"{k}={v}" for k, v in r.items()) for r in rows)
    return f"{MEMORABLE[template_id]} {' '.join(str(v) for v in params.values())}: {body}"[:600]


class OrgMemory:
    name = "org_memory"

    def __init__(self, hmem: HMem, trace):
        self.hmem, self.trace = hmem, trace

    def recall(self, state) -> list[dict]:
        """One hybrid query per memorable register, restricted to the affected system."""
        system = state.incident.get("system_id", "")
        recalled, scores = [], {}
        for template_id, label in MEMORABLE.items():
            q = f"{label} {system}"
            res = self.hmem.retrieval.query(q, require_tags=[f"system:{system}", f"template:{template_id}"])
            scores.update(res["trace"]["scores"])
            recalled += [m["memory"] for m in res["memories"] if not m["memory"].get("supersededBy")]
        self.trace.event(self.name, "recall", {"system": system, "scores": scores,
                                               "recalled": [m["id"] for m in recalled]})
        return recalled

    def learn(self, state, investigator) -> list[str]:
        system = state.incident.get("system_id", "")
        known = {(t.split(":", 1)[1], m["provenance"]["refs"][2]) for m in self.hmem.store.all()
                 if not m.get("supersededBy") for t in m["tags"] if t.startswith("params:")}
        added = []
        for f in state.findings:
            if f.status != "verified":
                continue
            for ev in f.evidence:
                res = investigator.results.get(ev.query_hash)
                if not res or ev.template_id not in MEMORABLE or not res.rows:
                    continue
                rh = rows_hash(res.rows)
                if (res.query_hash, rh) in known:
                    continue
                known.add((res.query_hash, rh))
                rec = self.hmem.store.add(
                    describe(ev.template_id, res.params, res.rows), source=f"breach72:{state.run_id}:{f.id}",
                    type_="factual", tags=[f"system:{system}", f"template:{ev.template_id}",
                                           f"params:{res.query_hash}"],
                    provenance={"origin": "document", "learnedAt": self.hmem.store.clock(),
                                "label": f"{state.run_id}/{f.id}",
                                "refs": [ev.template_id, canonical_json(res.params), rh, res.backend]})
                added.append(rec["id"])
        self.trace.event(self.name, "learn", {"added": added, "ledger": self.hmem.ledger.stats()})
        return added
