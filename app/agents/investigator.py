"""Investigator: answers each evidence question with vetted SQL templates executed
through the data backend (Zetaris, its replay, or the local dev backend).

Every finding carries provenance: backend, template id, tables, row reference,
query hash and execution time. No model is involved in reading the data.
"""
from __future__ import annotations

from app.adapters.base import QueryResult, SourceError
from app.orchestration.state import CaseState, Evidence, Finding  # noqa: F401

OUTBOUND = ("email_send", "outbound_transfer", "directory_listing")


def evidence_of(res: QueryResult, row_ref: str) -> Evidence:
    return Evidence(res.backend, res.template_id, res.tables, row_ref, res.query_hash, res.executed_at,
                    res.recorded_at)


class Investigator:
    name = "investigator"

    def __init__(self, backend, trace):
        self.backend, self.trace = backend, trace
        self.results: dict[str, QueryResult] = {}   # query_hash -> result (read by organisational memory)

    def q(self, state: CaseState, template_id: str, params: dict) -> QueryResult | None:
        try:
            res = self.backend.query(template_id, params)
        except SourceError as exc:
            state.errors.append({"type": "source_error", "message": str(exc), "recoverable": exc.recoverable,
                                 "template_id": template_id})
            self.trace.event(self.name, "query_failed", {"template_id": template_id, "params": params,
                                                         "error": str(exc), "recoverable": exc.recoverable})
            return None
        self.results[res.query_hash] = res
        self.trace.event(self.name, "query", {"template_id": template_id, "params": params, "backend": res.backend,
                                              "query_hash": res.query_hash, "rows": len(res.rows),
                                              "elapsed_ms": res.elapsed_ms, "recorded_at": res.recorded_at})
        return res

    # ---- incident core -------------------------------------------------
    def load_incident(self, state: CaseState) -> bool:
        res = self.q(state, "T01_INCIDENT_CORE", {"incident_id": state.incident_id})
        if not res or not res.rows:
            state.open_questions.append(f"Incident {state.incident_id} not found in the incident system.")
            return False
        state.incident = dict(res.rows[0])
        self.incident_evidence = evidence_of(res, state.incident_id)
        aware, assumed, ev = state.incident.get("aware_at"), False, [evidence_of(res, state.incident_id)]
        if not aware:
            tr = self.q(state, "T03_TRANSFER_EVENTS", {"incident_id": state.incident_id})
            candidates = [state.incident["reported_at"]] + ([r["ts"] for r in tr.rows] if tr else [])
            aware, assumed = min(candidates), True
            if tr and tr.rows:
                ev.append(evidence_of(tr, ",".join(r["log_id"] for r in tr.rows)))
        state.add(Finding("", "aware_at", "Moment the controller became aware of the breach"
                          + (" (assumed: earliest reliable record)" if assumed else ""),
                          {"aware_at": aware, "assumed": assumed}, ev, decisive=True))
        return True

    # ---- questions -----------------------------------------------------
    def answer(self, state: CaseState, question: dict) -> None:
        handler = getattr(self, f"_{question['claim']}")
        handler(state)

    def _data_categories(self, state: CaseState) -> None:
        res = self.q(state, "T05_ROPA_FOR_SYSTEM", {"system_id": state.incident["system_id"]})
        if not res or not res.rows:
            return
        cats = sorted({c for r in res.rows for c in r["data_categories"].split(";")})
        special = any(r["special_categories"] == "yes" for r in res.rows)
        procs = [r["processing_id"] for r in res.rows]
        state.add(Finding("", "data_categories", f"Data categories in {', '.join(procs)}: {', '.join(cats)}"
                          + ("; includes special categories" if special else ""),
                          {"categories": cats, "special": special, "processing_ids": procs,
                           "processor_ids": [r["processor_id"] for r in res.rows if r.get("processor_id")],
                           "subjects_estimate": max(int(r["subjects_estimate"]) for r in res.rows)},
                          [evidence_of(res, ",".join(procs))], decisive=True))

    def _transfers(self, state: CaseState) -> QueryResult | None:
        return self.q(state, "T03_TRANSFER_EVENTS", {"incident_id": state.incident_id})

    def _affected_count(self, state: CaseState) -> None:
        res = self._transfers(state)
        out = [r for r in (res.rows if res else []) if r["action"] in OUTBOUND and int(r["records_count"] or 0) > 0]
        if out:
            n = max(int(r["records_count"]) for r in out)
            state.add(Finding("", "affected_count", f"{n} records left the system in the logged event(s)",
                              {"subjects": n, "basis": "access_log"},
                              [evidence_of(res, ",".join(r["log_id"] for r in out))], decisive=True))
            return
        cat = state.get("data_categories")
        if cat:
            n = cat.value["subjects_estimate"]
            state.add(Finding("", "affected_count", f"Up to {n} data subjects (ROPA estimate; no record count "
                              "in the logs)", {"subjects": n, "basis": "ropa_estimate"}, list(cat.evidence),
                              decisive=True, note="upper bound"))

    def _disclosure(self, state: CaseState) -> None:
        res = self._transfers(state)
        ext = [r for r in (res.rows if res else []) if r["action"] in OUTBOUND]
        category = state.incident.get("category")
        if ext:
            dest = sorted({r["destination"] for r in ext})
            state.add(Finding("", "disclosure", f"Data sent outside the controller: {', '.join(r['action'] for r in ext)}"
                              f" to {', '.join(dest)}",
                              {"left_control": True, "recipient": "unknown", "destinations": dest,
                               "actions": [r["action"] for r in ext],
                               "bytes": sum(int(r["bytes"] or 0) for r in ext)},
                              [evidence_of(res, ",".join(r["log_id"] for r in ext))], decisive=True))
        elif category in ("lost_device", "processor_breach"):
            basis = "device lost outside the premises" if category == "lost_device" else \
                "unauthorised access reported by the processor"
            state.add(Finding("", "disclosure", f"Loss of control: {basis}",
                              {"left_control": True, "recipient": "unknown", "destinations": [], "actions": []},
                              [self.incident_evidence], decisive=True))
        else:
            state.add(Finding("", "disclosure", "No outbound event linked to the incident in the access log",
                              {"left_control": False, "recipient": None, "destinations": [], "actions": []},
                              [evidence_of(res, "none")] if res else [], decisive=True))

    def _data_unintelligible(self, state: CaseState) -> None:
        if state.incident.get("category") == "misdirected_email":
            res = self._transfers(state)
            sent = [r for r in (res.rows if res else []) if r["action"] == "email_send"]
            if sent:
                enc = all(r["attachment_encrypted"] == "yes" for r in sent)
                state.add(Finding("", "data_unintelligible", "The e-mailed file was "
                                  + ("encrypted" if enc else "NOT encrypted"),
                                  {"unintelligible": enc, "basis": "attachment_encrypted"},
                                  [evidence_of(res, ",".join(r["log_id"] for r in sent))], decisive=True))
            return
        res = self.q(state, "T06_ASSET_SECURITY", {"system_id": state.incident["system_id"]})
        if not res or not res.rows:
            return
        a = res.rows[0]
        enc = a["encryption_at_rest"] not in ("none", "", None) and str(a["key_location"]).startswith("separate")
        state.add(Finding("", "data_unintelligible",
                          f"Encryption at rest: {a['encryption_at_rest']}, key: {a['key_location']}"
                          + (" (unintelligible to a finder)" if enc else " (readable if accessed)"),
                          {"unintelligible": enc, "basis": "asset_register"}, [evidence_of(res, a["system_id"])],
                          decisive=True))

    def _availability(self, state: CaseState) -> None:
        res = self.q(state, "T06_ASSET_SECURITY", {"system_id": state.incident["system_id"]})
        if not res or not res.rows:
            return
        a = res.rows[0]
        category = state.incident.get("category")
        if a["backup"] in ("none", "") or (a["backup_offline"] == "no" and category == "ransomware"):
            level = "permanent"
        elif category == "ransomware":
            level = "recoverable_delay"
        else:
            level = "none"
        state.add(Finding("", "availability", f"Backup: {a['backup']}, offline copy: {a['backup_offline']} "
                          f"-> availability impact '{level}'", {"level": level},
                          [evidence_of(res, a["system_id"])], decisive=False))

    def _mitigations(self, state: CaseState) -> None:
        res = self.q(state, "T02_INCIDENT_UPDATES", {"incident_id": state.incident_id, "as_of": state.as_of})
        if res is None:
            return
        codes = [r["code"] for r in res.rows]
        state.add(Finding("", "mitigations", "Updates recorded up to the assessment time: "
                          + (", ".join(codes) if codes else "none"),
                          {"codes": codes, "updates": res.rows},
                          [evidence_of(res, ",".join(r["update_id"] for r in res.rows) or "none")]))

    def _processor(self, state: CaseState) -> None:
        if not state.get("data_categories"):
            self._data_categories(state)
        cat = state.get("data_categories")
        pids = cat.value["processor_ids"] if cat else []
        if not pids:
            return
        res = self.q(state, "T07_PROCESSOR_CONTRACT", {"processor_id": pids[0]})
        upd = self.q(state, "T02_INCIDENT_UPDATES", {"incident_id": state.incident_id, "as_of": state.as_of})
        detected = next((r["ts"] for r in (upd.rows if upd else []) if r["code"] == "PROCESSOR_DETECTED"), None)
        if res and res.rows:
            c = res.rows[0]
            ev = [evidence_of(res, c["processor_id"])] + ([evidence_of(upd, "PROCESSOR_DETECTED")] if detected else [])
            state.add(Finding("", "processor", f"Processor {c['name']} ({c['contract_ref']}), Art. 28 contract: "
                              f"{c['art28_contract_signed']}, notice within {c['breach_notice_hours']} h",
                              {"processor_id": c["processor_id"], "notice_hours": int(c["breach_notice_hours"]),
                               "detected_at": detected}, ev, decisive=True))
