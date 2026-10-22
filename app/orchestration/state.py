"""Shared case state read and written by all agents.

Findings live in the Meterless World Model (app/adapters/meterless_world.py):
every new finding and every status change made by the Evidence Auditor is
asserted as a fact about the incident entity, with provenance (agent, template,
query hash). Agents read the current finding for a claim through the World Model;
superseded versions stay in the event log as the evidence trail.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any

CONFIDENCE = {"high": 0.9, "single_source": 0.6, "unverified": 0.5, "low": 0.3, "none": 0.0}


@dataclass
class Evidence:
    backend: str
    template_id: str
    tables: list[str]
    row_ref: str
    query_hash: str
    executed_at: str
    recorded_at: str | None = None


@dataclass
class Finding:
    id: str
    claim: str             # machine key, e.g. "affected_count"
    statement: str         # human-readable claim
    value: Any
    evidence: list[Evidence] = field(default_factory=list)
    status: str = "proposed"   # proposed | verified | conflict | vetoed
    verified_by: str | None = None
    confidence: str = "unverified"
    decisive: bool = False
    note: str = ""


@dataclass
class CaseState:
    run_id: str
    incident_id: str
    as_of: str
    dpo_contact: str = ""
    organisation: str = ""
    incident: dict = field(default_factory=dict)
    questions: list[dict] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    open_questions: list[str] = field(default_factory=list)
    iterations: int = 0
    vetoes: list[dict] = field(default_factory=list)
    errors: list[dict] = field(default_factory=list)
    agents_used: list[str] = field(default_factory=list)
    world: Any = None                      # meterless_world.WorldModel
    _synced: dict = field(default_factory=dict)

    @property
    def incident_entity(self) -> str:
        return f"incident:breach72:{self.incident_id}"

    @property
    def case_context(self) -> str:
        return f"case:breach72:{self.run_id}"

    def get(self, claim: str) -> Finding | None:
        if self.world is not None:
            by_id = {f.id: f for f in self.findings}
            for fact in reversed(self.world.history(self.incident_entity, claim)):
                f = by_id.get(fact["value"]["finding_id"])
                if f is not None and f.status != "vetoed":
                    return f
            return None
        for f in reversed(self.findings):
            if f.claim == claim and f.status != "vetoed":
                return f
        return None

    def add(self, finding: Finding, by: str = "investigator") -> Finding:
        finding.id = f"F{len(self.findings) + 1:02d}"
        self.findings.append(finding)
        self._assert(finding, by)
        return finding

    def sync_world(self, by: str) -> int:
        """Assert every finding whose status, confidence or value changed. Returns the count."""
        return sum(self._assert(f, by) for f in self.findings)

    def _assert(self, f: Finding, by: str) -> bool:
        if self.world is None:
            return False
        value = {"finding_id": f.id, "statement": f.statement, "value": f.value, "status": f.status,
                 "confidence": f.confidence, "verified_by": f.verified_by, "note": f.note,
                 "evidence": [e.query_hash for e in f.evidence]}
        sig = repr(sorted(value.items(), key=lambda kv: kv[0]))
        if self._synced.get(f.id) == sig:
            return False
        self._synced[f.id] = sig
        ev = f.evidence[-1] if f.evidence else None
        self.world.assert_fact(
            self.incident_entity, f.claim, value, context=self.case_context,
            confidence=CONFIDENCE.get(f.confidence, 0.5),
            source={"kind": "agent", "by": by, "runId": self.run_id,
                    "at": datetime.now(timezone.utc).isoformat(timespec="microseconds"),
                    "url": f"breach72://{ev.backend}/{ev.template_id}" if ev else None,
                    "checksum": ev.query_hash if ev else None})
        return True

    def findings_dict(self) -> list[dict]:
        return [asdict(f) for f in self.findings]
