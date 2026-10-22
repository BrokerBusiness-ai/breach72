"""Shared case state read and written by all agents.

This in-process store is the interface the Meterless World Model adapter will
implement (build block B8): entities (incident, systems), findings with their
evidence trail, and the decision record.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any


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

    def get(self, claim: str) -> Finding | None:
        for f in reversed(self.findings):
            if f.claim == claim and f.status != "vetoed":
                return f
        return None

    def add(self, finding: Finding) -> Finding:
        finding.id = f"F{len(self.findings) + 1:02d}"
        self.findings.append(finding)
        return finding

    def findings_dict(self) -> list[dict]:
        return [asdict(f) for f in self.findings]
