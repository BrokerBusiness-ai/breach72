"""Risk Assessor: deterministic. Turns audited findings into rule inputs and runs
app/rules (ENISA severity, GDPR Art. 33/34 paths). No language model.

Mapping of data categories to the ENISA data type (documented policy):
  special categories or health data         -> sensitive
  national identifier (PESEL), bank account,
  salary                                    -> financial (identity fraud / financial loss)
  free text, work documents, benefit data   -> behavioural
  otherwise                                 -> simple
"""
from __future__ import annotations

from datetime import datetime

from app.orchestration.state import CaseState
from app.rules.enisa_severity import BreachFacts, assess
from app.rules.gdpr_paths import CaseContext, decide

SENSITIVE = {"sick_leave_certificates", "health_data"}
FINANCIAL = {"pesel", "bank_account", "salary", "id_card_number"}
BEHAVIOURAL = {"message_text", "work_documents", "benefit_type"}
IDENTIFYING = {"name", "pesel", "email", "id_card_number"}
MALICIOUS_CATEGORIES = {"ransomware", "processor_breach"}


def _usable(f):
    return f is not None and f.status in ("verified", "conflict")


class RiskAssessor:
    name = "risk_assessor"

    def __init__(self, trace):
        self.trace = trace

    def facts_from_state(self, state: CaseState) -> tuple[BreachFacts, CaseContext, list[str]]:
        gaps = []
        cat = state.get("data_categories")
        cats = set(cat.value["categories"]) if _usable(cat) else set()
        if not _usable(cat):
            gaps.append("data_categories")
        special = bool(cat and cat.value.get("special")) or bool(cats & SENSITIVE)
        if special:
            data_type = "sensitive"
        elif cats & FINANCIAL:
            data_type = "financial"
        elif cats & BEHAVIOURAL:
            data_type = "behavioural"
        else:
            data_type = "simple"

        disc = state.get("disclosure")
        unint = state.get("data_unintelligible")
        avail = state.get("availability")
        mit = state.get("mitigations")
        codes = mit.value["codes"] if mit else []
        category = state.incident.get("category", "")

        if not _usable(disc):
            gaps.append("disclosure")
            conf = "unknown_recipients"  # conservative when unknown
        elif not disc.value["left_control"]:
            conf = "none"
        elif disc.value.get("recipient") == "identified":
            conf = "known_recipients"
        else:
            conf = "unknown_recipients"
        mitigations = ("recipient_deletion_confirmed",) if "RECIPIENT_DELETION_CONFIRMED" in codes else ()
        if mitigations and conf == "known_recipients":
            conf = "unknown_recipients"  # the rule itself applies the mitigation step, once
        unintelligible = bool(_usable(unint) and unint.value["unintelligible"])
        if not _usable(unint):
            gaps.append("data_unintelligible")
        malicious = category in MALICIOUS_CATEGORIES or bool(
            _usable(disc) and "directory_listing" in disc.value.get("actions", []))

        facts = BreachFacts(
            data_type=data_type,
            identifiability="maximum" if cats & IDENTIFYING or not cats else "limited",
            unintelligible=unintelligible,
            confidentiality=conf,
            availability=avail.value["level"] if _usable(avail) else "none",
            malicious=malicious,
            mitigations=mitigations,
        )
        aware = state.get("aware_at")
        proc = state.get("processor")
        ctx = CaseContext(
            aware_at=datetime.fromisoformat(aware.value["aware_at"]),
            aware_assumed=aware.value["assumed"],
            now=datetime.fromisoformat(state.as_of),
            unintelligible=unintelligible,
            special_categories=special,
            facts_incomplete=bool(gaps) or any(f.status == "conflict" for f in state.findings),
            sources_conflict=any(f.status == "conflict" and f.decisive for f in state.findings),
            via_processor=_usable(proc),
            processor_detected_at=datetime.fromisoformat(proc.value["detected_at"])
            if _usable(proc) and proc.value.get("detected_at") else None,
            processor_notice_hours_contract=proc.value["notice_hours"] if _usable(proc) else None,
        )
        return facts, ctx, gaps

    def assess(self, state: CaseState) -> dict:
        facts, ctx, gaps = self.facts_from_state(state)
        sev = assess(facts)
        path = decide(sev, ctx)
        self.trace.event(self.name, "severity", {"facts": facts.__dict__, "score": sev.score, "band": sev.band,
                                                 "components": sev.components, "borderline": sev.borderline})
        self.trace.event(self.name, "decision", {"decision": path.decision, "provisional": path.provisional_decision,
                                                 "notify_sa": path.notify_sa, "notify_subjects": path.notify_subjects,
                                                 "escalation_reasons": path.escalation_reasons,
                                                 "flags": path.flags})
        return {"facts": facts, "severity": sev, "path": path, "gaps": gaps}
