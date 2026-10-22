"""Planner: takes the incident, fixes the awareness moment, and orders the evidence
questions by their impact on the decision.

The question list is a fixed catalogue (deterministic). Nemotron (small model)
only writes a short investigation note for the human reader.
"""
from __future__ import annotations

from app.orchestration.state import CaseState

# claim -> (question, decision impact 1..3)
CATALOGUE = {
    "data_categories": ("Which categories of personal data are involved, any special categories (Art. 9)?", 3),
    "affected_count": ("How many data subjects and records are affected?", 3),
    "disclosure": ("Did the data leave the controller's control, and to whom?", 3),
    "data_unintelligible": ("Were the data unintelligible to the recipient (encryption, key location)?", 3),
    "availability": ("Is availability lost, and can the data be restored from backup?", 2),
    "mitigations": ("What measures were taken since awareness (updates up to the assessment time)?", 2),
    "processor": ("Is a processor involved and when did it detect and report the breach?", 2),
}
BY_CATEGORY = {
    "misdirected_email": ["data_categories", "affected_count", "disclosure", "data_unintelligible", "mitigations"],
    "lost_device": ["data_categories", "data_unintelligible", "disclosure", "availability", "affected_count",
                    "mitigations"],
    "ransomware": ["data_categories", "disclosure", "availability", "data_unintelligible", "affected_count",
                   "mitigations"],
    "processor_breach": ["processor", "data_categories", "disclosure", "affected_count", "data_unintelligible",
                         "mitigations"],
    "misconfiguration": ["data_categories", "disclosure", "affected_count", "data_unintelligible", "mitigations"],
}


class Planner:
    name = "planner"

    def __init__(self, llm, trace):
        self.llm, self.trace = llm, trace

    def plan(self, state: CaseState, retry_claims: list[str] | None = None) -> list[dict]:
        if retry_claims:
            qs = [{"claim": c, "question": CATALOGUE[c][0], "impact": CATALOGUE[c][1], "retry": True}
                  for c in retry_claims if c in CATALOGUE]
            self.trace.event(self.name, "replan", {"claims": [q["claim"] for q in qs], "iteration": state.iterations})
            return qs
        category = state.incident.get("category", "")
        claims = BY_CATEGORY.get(category, list(CATALOGUE))
        qs = sorted(({"claim": c, "question": CATALOGUE[c][0], "impact": CATALOGUE[c][1]} for c in claims),
                    key=lambda q: -q["impact"])
        state.questions = qs
        self.trace.event(self.name, "plan", {"category": category, "questions": [q["claim"] for q in qs]})
        note = self.llm.chat(self.name, "small",
                             "You are the planning agent of a GDPR breach investigation. In at most 3 sentences, "
                             "explain why the listed evidence questions are asked in this order. Do not assess "
                             "the breach and do not suggest a decision.",
                             f"Incident category: {category}. Summary: {state.incident.get('summary', '')}. "
                             f"Questions in order: {[q['question'] for q in qs]}")
        if note:
            state.incident["planner_note"] = note.strip()
        return qs
