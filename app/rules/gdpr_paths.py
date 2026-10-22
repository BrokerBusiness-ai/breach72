"""Maps a severity assessment and case context to GDPR Articles 33 and 34.

Policy (documented, deterministic, unit-tested against EDPB Guidelines 01/2021):
  low        -> breach unlikely to result in a risk: document only, Art. 33(5)
  medium     -> risk: notify the supervisory authority, Art. 33(1)
  high/v.high-> high risk: also communicate to data subjects, Art. 34(1),
                unless Art. 34(3)(a) (data unintelligible) or 34(3)(b) applies
Escalation to the DPO (human decision) when the score is borderline, sources
conflict, or the awareness time had to be assumed.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta

from app.rules.enisa_severity import Severity

DOC_ONLY = "document_internally"
NOTIFY_SA = "notify_authority"
NOTIFY_SA_AND_DS = "notify_authority_and_individuals"
ESCALATE = "escalate_to_dpo"


@dataclass
class CaseContext:
    aware_at: datetime
    aware_assumed: bool = False
    now: datetime | None = None
    unintelligible: bool = False
    special_categories: bool = False
    facts_incomplete: bool = False
    sources_conflict: bool = False
    via_processor: bool = False
    processor_detected_at: datetime | None = None
    processor_notice_hours_contract: int | None = None


@dataclass
class Path:
    decision: str
    provisional_decision: str
    notify_sa: bool
    notify_subjects: bool
    legal_basis: list[dict] = field(default_factory=list)
    escalation_reasons: list[str] = field(default_factory=list)
    clock: dict = field(default_factory=dict)
    flags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def clock(ctx: CaseContext) -> dict:
    deadline = ctx.aware_at + timedelta(hours=72)
    out = {"aware_at": ctx.aware_at.isoformat(), "assumed": ctx.aware_assumed, "deadline": deadline.isoformat()}
    if ctx.now is not None:
        out["now"] = ctx.now.isoformat()
        out["hours_left"] = round((deadline - ctx.now).total_seconds() / 3600, 1)
    return out


def decide(sev: Severity, ctx: CaseContext) -> Path:
    basis: list[dict] = []
    flags: list[str] = []
    if sev.band == "low":
        provisional, sa, ds = DOC_ONLY, False, False
        basis.append({"article": "GDPR Art. 33(1)", "why": "breach unlikely to result in a risk (severity low)"})
    elif sev.band == "medium":
        provisional, sa, ds = NOTIFY_SA, True, False
        basis.append({"article": "GDPR Art. 33(1)", "why": "risk to rights and freedoms (severity medium)"})
    else:
        provisional, sa, ds = NOTIFY_SA_AND_DS, True, True
        basis.append({"article": "GDPR Art. 33(1)", "why": f"risk to rights and freedoms (severity {sev.band})"})
        basis.append({"article": "GDPR Art. 34(1)", "why": f"likely high risk (severity {sev.band})"})
        if ctx.unintelligible:
            provisional, ds = NOTIFY_SA, False
            basis.append({"article": "GDPR Art. 34(3)(a)", "why": "data unintelligible to unauthorised persons"})
    basis.append({"article": "GDPR Art. 33(5)", "why": "every breach is documented internally"})

    if ctx.special_categories:
        flags.append("special_categories_art9")
        basis.append({"article": "GDPR Art. 9(1)", "why": "special categories involved: aggravating factor"})
    if sa and ctx.facts_incomplete:
        flags.append("phased_notification")
        basis.append({"article": "GDPR Art. 33(4)", "why": "information not yet complete: notify in phases"})
    c = clock(ctx)
    if sa and c.get("hours_left") is not None and c["hours_left"] < 0:
        flags.append("late_notification_reasons_required")
        basis.append({"article": "GDPR Art. 33(1)", "why": "notification after 72 hours must state reasons for delay"})
    if ctx.via_processor:
        flags.append("processor_breach")
        basis.append({"article": "GDPR Art. 33(2)", "why": "processor must notify controller without undue delay"})
        if ctx.processor_detected_at is not None:
            lag = (ctx.aware_at - ctx.processor_detected_at).total_seconds() / 3600
            c["processor_lag_hours"] = round(lag, 1)
            limit = ctx.processor_notice_hours_contract
            if limit is not None and lag > limit:
                flags.append("processor_contract_breach_review")
                basis.append({"article": "GDPR Art. 28(3)(f)",
                              "why": f"processor informed after {lag:.0f} h, contract allows {limit} h: review contract"})

    reasons = []
    if sev.borderline:
        reasons.append(f"severity score {sev.score} is near a band threshold")
    if ctx.sources_conflict:
        reasons.append("sources conflict on a decisive fact")
    if ctx.aware_assumed:
        reasons.append("awareness time not recorded; earliest reliable time assumed")
    decision = ESCALATE if reasons else provisional
    return Path(decision, provisional, sa, ds, basis, reasons, c, flags)
