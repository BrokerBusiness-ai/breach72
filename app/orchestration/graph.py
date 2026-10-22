"""Orchestration: Planner -> Investigator -> Evidence Auditor (veto loop) ->
Risk Assessor -> Reviewer & Drafter. At most MAX_ITERATIONS audit passes.
"""
from __future__ import annotations

import os
import time
import uuid
from dataclasses import asdict
from pathlib import Path

from app.adapters.base import ROOT, SourceError
from app.adapters.nemotron import NemotronClient
from app.agents.auditor import EvidenceAuditor
from app.agents.investigator import Investigator
from app.agents.planner import Planner
from app.agents.reviewer import ReviewerDrafter
from app.agents.risk_assessor import RiskAssessor
from app.orchestration.state import CaseState
from app.orchestration.trace import Trace

MAX_ITERATIONS = 3

LIMITATIONS = [
    "Decision support for a data protection officer, not legal advice.",
    "All data is synthetic (see data/SYNTHETIC.md).",
    "Severity follows the ENISA 2013 method; national supervisory authority practice may differ.",
]


def make_backend(name: str):
    if name == "dev":
        from app.adapters.dev_sqlite import DevSQLiteBackend
        return DevSQLiteBackend()
    if name == "replay":
        from app.adapters.zetaris_replay import ZetarisReplayBackend
        return ZetarisReplayBackend()
    if name == "zetaris":
        from app.adapters.zetaris import ZetarisBackend
        return ZetarisBackend()
    raise ValueError(f"unknown backend {name}")


def default_backend() -> str:
    return "replay" if os.environ.get("SAMPLE_MODE", "true").lower() == "true" else "zetaris"


def run_case(case: dict, backend: str | None = None, log_dir: Path | None = None) -> dict:
    t0 = time.perf_counter()
    run_id = case.get("run_id") or time.strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:6]
    trace = Trace(run_id, log_dir or ROOT / "logs")
    backend = backend or default_backend()
    state = CaseState(run_id, case["incident_id"], case["as_of"], case.get("dpo_contact", ""),
                      case.get("organisation", ""))
    trace.event("orchestrator", "start", {"incident_id": state.incident_id, "as_of": state.as_of,
                                          "backend": backend})
    try:
        be = make_backend(backend)
    except SourceError as exc:
        return _error(run_id, trace, "source_unavailable", str(exc), exc.recoverable, t0)

    llm = NemotronClient(trace)
    planner, inv = Planner(llm, trace), Investigator(be, trace)
    auditor, assessor, reviewer = EvidenceAuditor(inv, llm, trace), RiskAssessor(trace), ReviewerDrafter(llm, trace)
    state.agents_used = [planner.name, inv.name, auditor.name, assessor.name, reviewer.name]

    if not inv.load_incident(state):
        if state.errors:
            err = state.errors[0]
            return _error(run_id, trace, "source_unavailable", err["message"], err["recoverable"], t0)
        return _error(run_id, trace, "incident_not_found", "; ".join(state.open_questions), False, t0)

    questions = planner.plan(state)
    while True:
        state.iterations += 1
        for q in questions:
            inv.answer(state, q)
        vetoed = auditor.audit(state)
        trace.event("orchestrator", "iteration_end", {"iteration": state.iterations, "vetoed": vetoed})
        if not vetoed or state.iterations >= MAX_ITERATIONS:
            if vetoed:
                state.open_questions += [f"No evidence for '{c}' after {MAX_ITERATIONS} iterations" for c in vetoed]
            break
        questions = planner.plan(state, retry_claims=vetoed)

    risk = assessor.assess(state)
    review = reviewer.review(state, risk)
    sev, path = risk["severity"], risk["path"]
    backends = sorted({e.backend for f in state.findings for e in f.evidence})
    out = {
        "run_id": run_id, "status": "ok", "incident_id": state.incident_id, "as_of": state.as_of,
        "decision": path.decision, "decision_text": review["decision_text"],
        "provisional_decision": path.provisional_decision,
        "notify_supervisory_authority": path.notify_sa, "notify_data_subjects": path.notify_subjects,
        "escalation_reasons": path.escalation_reasons, "flags": path.flags,
        "risk": {"score": sev.score, "band": sev.band, "components": sev.components, "method": "ENISA 2013",
                 "rule_inputs": asdict(risk["facts"])},
        "explanation": review["explanation"], "model_summary": review["summary"],
        "findings": state.findings_dict(), "legal_basis": path.legal_basis,
        "draft_notification": review["draft"], "open_questions": review["open_questions"],
        "clock": path.clock, "limitations": LIMITATIONS + (
            ["Evidence came from the local dev backend, not from Zetaris."] if "dev-sqlite" in backends else []),
        "data_backends": backends, "agents": state.agents_used, "iterations": state.iterations,
        "vetoes": state.vetoes, "errors": state.errors, "llm_enabled": llm.enabled,
        "trace_id": run_id, "log_file": trace.path.relative_to(ROOT).as_posix() if trace.path.is_relative_to(ROOT)
        else str(trace.path), "execution_time": round(time.perf_counter() - t0, 3),
    }
    trace.event("orchestrator", "end", {"decision": path.decision, "execution_time": out["execution_time"]})
    return out


def _error(run_id, trace, etype, message, recoverable, t0) -> dict:
    trace.event("orchestrator", "error", {"type": etype, "message": message, "recoverable": recoverable})
    return {"run_id": run_id, "status": "error", "error": {"type": etype, "message": message,
                                                            "recoverable": recoverable},
            "trace_id": run_id, "log_file": str(trace.path), "execution_time": round(time.perf_counter() - t0, 3)}
