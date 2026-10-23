"""Reviewer & Drafter: checks the decision against the rule output, writes the
explanation (each sentence cites finding ids and legal text), drafts the Art. 33(3)
notification and Art. 34 communication, lists open questions, and escalates to
the DPO when confidence is low.

Nemotron (large model) may write a plain-language summary. The summary is accepted
only if every finding id it cites exists and it does not contradict the decision;
otherwise it is discarded and the rejection is logged.
"""
from __future__ import annotations

import re

from app.orchestration.state import CaseState
from app.rules.art33_fields import build_draft

DECISION_TEXT = {
    "document_internally": "Document internally only (no notification)",
    "notify_authority": "Notify the supervisory authority",
    "notify_authority_and_individuals": "Notify the supervisory authority and communicate to the data subjects",
    "escalate_to_dpo": "Escalate to the DPO for a human decision",
}
CONSEQUENCES = {
    "pesel": "identity fraud using the national identification number",
    "bank_account": "financial fraud",
    "salary": "disclosure of financial situation",
    "benefit_type": "disclosure of reliance on social assistance (stigma, discrimination)",
    "sick_leave_certificates": "disclosure of health-related information",
    "message_text": "disclosure of the content of private messages",
    "email": "phishing targeting the data subjects",
}
MEASURES = {
    "REMOTE_WIPE_SENT": "remote wipe of the device initiated",
    "RECIPIENT_IDENTIFIED": "recipient identified and contacted",
    "RECIPIENT_DELETION_CONFIRMED": "recipient confirmed deletion in writing",
    "BACKUP_RESTORE_STARTED": "restore from offline backup started",
    "IT_REPORT_NO_EXFIL": "initial IT forensic report obtained",
    "PROCESSOR_NOTICE": "processor notice received",
}
PROPOSED = {
    "misdirected_email": "identify and contact the recipient, request written confirmation of deletion; "
                         "enforce encryption of e-mailed exports",
    "lost_device": "confirm remote wipe; rotate credentials stored on the device",
    "ransomware": "isolate affected systems, restore from offline backup, complete forensic analysis of exfiltration",
    "processor_breach": "obtain the processor's full incident report; review the Art. 28 contract",
    "misconfiguration": "disable public listing, review access logs for downloads, rotate exposed secrets",
}
_FID = re.compile(r"\bF\d{2}\b")
# Statements a summary may not make for a given decision (checked case-insensitively).
_NEG_NOTIFY = r"(no|not|without|need not|needn't|do(es)? not need to)\s+(\w+\s+){0,3}notif"
_CONTRADICTIONS = {
    "document_internally": [r"\bmust (be )?notif", r"\bshould (be )?notif", r"\brequires? notification"],
    "notify_authority": [_NEG_NOTIFY + r"\w*\s+(\w+\s+){0,3}(supervisory|authority|UODO)",
                         r"\bonly (be )?document", r"\b(must|should) (also )?(communicate|inform|notify) "
                         r"(\w+\s+){0,2}(data subjects|individuals)"],
    "notify_authority_and_individuals": [_NEG_NOTIFY, r"\bonly (be )?document",
                                         r"\bno (need|requirement) to (communicate|inform)"],
    "escalate_to_dpo": [r"\bfinal decision is\b", r"\bno (further )?review (is )?needed"],
}


def contradicts(decision: str, text: str) -> str | None:
    """Return the first forbidden pattern found in `text` for `decision`, else None."""
    for pat in _CONTRADICTIONS.get(decision, []):
        if re.search(pat, text, flags=re.IGNORECASE):
            return pat
    return None


class ReviewerDrafter:
    name = "reviewer_drafter"

    def __init__(self, llm, trace):
        self.llm, self.trace = llm, trace

    def review(self, state: CaseState, risk: dict) -> dict:
        sev, path = risk["severity"], risk["path"]
        f = {x.claim: x for x in state.findings if x.status != "vetoed"}
        cite = lambda claim: f"[{f[claim].id}]" if claim in f else "[no evidence]"

        expl = [
            f"Severity {sev.score} ({sev.band}) = DPC {sev.dpc} x EI {sev.ei} + CB {sev.cb} "
            f"(ENISA 2013) {cite('data_categories')}{cite('data_unintelligible')}{cite('disclosure')}.",
        ]
        expl += [f"Rule step: {r}." for r in sev.reasons]
        for b in path.legal_basis:
            expl.append(f"{b['article']}: {b['why']}.")
        if path.escalation_reasons:
            expl.append("Escalated to the DPO because: " + "; ".join(path.escalation_reasons) + ".")

        # consistency check: decision must follow from the rule output, nothing else
        expected_sa = sev.band != "low"
        consistent = path.notify_sa == expected_sa
        self.trace.event(self.name, "consistency_check", {"consistent": consistent, "band": sev.band,
                                                          "notify_sa": path.notify_sa})
        if not consistent:
            path.escalation_reasons.append("reviewer found the decision inconsistent with the rule output")
            path.decision = "escalate_to_dpo"

        cat, cnt = f.get("data_categories"), f.get("affected_count")
        mit = f.get("mitigations")
        nature = None
        if cat and cnt and cat.status == "verified" and cnt.status in ("verified", "conflict"):
            nature = (f"{state.incident.get('category', '').replace('_', ' ')}: {state.incident.get('summary', '')} "
                      f"Categories: {', '.join(cat.value['categories'])}. Approximately {cnt.value['subjects']} data "
                      f"subjects and records ({cnt.value['basis'].replace('_', ' ')}).")
        cons = sorted({CONSEQUENCES[c] for c in (cat.value["categories"] if cat else []) if c in CONSEQUENCES})
        measures = [MEASURES[c] for c in (mit.value["codes"] if mit else []) if c in MEASURES]
        proposed = PROPOSED.get(state.incident.get("category", ""))
        measure_parts = ([f"Measures taken: {'; '.join(measures)}"] if measures else []) + (
            [f"Proposed: {proposed}"] if proposed else [])
        draft = build_draft({
            "a_nature": nature,
            "b_contact": state.dpo_contact or None,
            "c_consequences": ("Likely consequences: " + "; ".join(cons) + ".") if cons else None,
            "d_measures": (". ".join(measure_parts) + ".") if measure_parts else None,
        }, notify_subjects=path.notify_subjects)
        if "late_notification_reasons_required" in path.flags:
            draft["reasons_for_delay"] = "[REQUIRED: reasons for notifying after 72 hours, Art. 33(1)]"
        if "phased_notification" in path.flags:
            draft["phased"] = "Initial notification; further information will follow without undue delay (Art. 33(4))."

        open_q = list(state.open_questions)
        open_q += [f"Resolve conflict in {x.id} ({x.claim}): {x.note}" for x in state.findings if x.status == "conflict"]
        open_q += [f"Missing evidence for {g}" for g in risk["gaps"]]
        open_q += [f"Draft field missing: {m}" for m in draft["missing"]]

        summary = self._summary(state, path, sev, expl)
        self.trace.event(self.name, "drafted", {"decision": path.decision, "draft_complete": draft["complete"],
                                                "open_questions": len(open_q)})
        return {"explanation": expl, "summary": summary, "draft": draft, "open_questions": open_q,
                "decision_text": DECISION_TEXT[path.decision]}

    def _summary(self, state, path, sev, expl) -> str | None:
        ids = {x.id for x in state.findings}
        if state.world is not None:   # Meterless ContextBuilder: current facts with provenance
            from app.adapters.meterless_world import ContextBuilder
            facts = ContextBuilder(state.world).build(state.incident_entity)["text"]
        else:
            facts = "\n".join(f"{x.id} [{x.status}]: {x.statement}" for x in state.findings if x.status != "vetoed")
        text = self.llm.chat(self.name, "large",
                             "You explain a decision that has ALREADY been made by a deterministic GDPR rule. "
                             "Write 3-5 sentences for a data protection officer. Cite finding ids like [F03] for "
                             "every factual statement. Do not change, soften or question the decision.",
                             f"Decision: {DECISION_TEXT[path.decision]} (provisional: {path.provisional_decision}). "
                             f"Severity {sev.score} {sev.band}.\nFindings:\n{facts}\nRule explanation:\n"
                             + "\n".join(expl), max_tokens=400)
        if not text:
            return None
        cited = set(_FID.findall(text))
        if not cited or not cited <= ids:
            self.trace.event(self.name, "llm_output_rejected", {"reason": "missing or unknown finding ids",
                                                                "cited": sorted(cited)})
            return None
        bad = contradicts(path.decision, text)
        if bad:
            self.trace.event(self.name, "llm_output_rejected", {"reason": "contradicts the decision",
                                                                "pattern": bad})
            return None
        self.trace.event(self.name, "llm_output_accepted", {"cited": sorted(cited)})
        return text.strip()
