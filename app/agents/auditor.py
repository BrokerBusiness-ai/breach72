"""Evidence Auditor: holds a veto over every claim.

- A decisive claim without evidence is vetoed and returned to the Planner.
- Key facts (number of people, encryption, exfiltration/disclosure, data categories)
  are re-verified at the source with a DIFFERENT template.
- Disagreement between sources opens a "conflict" branch: the conservative value
  is kept for the provisional decision and the case is escalated to the DPO.
- Facts recalled from organisational memory (Meterless H-MEM) are re-verified at
  the source with the template that produced them; the outcome is fed back to
  H-MEM (helpful + promotion, or wrong + a superseding record).

Nemotron (large model) may add a short written assessment of a conflict; it
cannot change a status.
"""
from __future__ import annotations

import json

from app.agents.investigator import evidence_of
from app.orchestration.state import CaseState, Finding


class EvidenceAuditor:
    name = "evidence_auditor"

    def __init__(self, investigator, llm, trace):
        self.inv, self.llm, self.trace = investigator, llm, trace

    def audit(self, state: CaseState) -> list[str]:
        """Returns the claims vetoed in this pass (for re-planning)."""
        vetoed = []
        for f in state.findings:
            if f.status != "proposed":
                continue
            if f.decisive and not f.evidence:
                f.status, f.confidence = "vetoed", "none"
                state.vetoes.append({"finding": f.id, "claim": f.claim, "reason": "no evidence"})
                self.trace.event(self.name, "veto", {"finding": f.id, "claim": f.claim, "reason": "no evidence"})
                vetoed.append(f.claim)
                continue
            check = getattr(self, f"_verify_{f.claim}", None)
            if check:
                check(state, f)
            else:
                f.status, f.confidence = "verified", "single_source"
            self.trace.event(self.name, "audited", {"finding": f.id, "claim": f.claim, "status": f.status,
                                                    "verified_by": f.verified_by, "confidence": f.confidence})
        for f in state.findings:
            if f.status == "conflict":
                note = self.llm.chat(self.name, "large",
                                     "You audit evidence in a GDPR breach investigation. Two sources disagree. "
                                     "In at most 2 sentences say which additional evidence would settle it. "
                                     "Do not decide the case.",
                                     f"Claim: {f.statement}. Conflict: {f.note}")
                if note:
                    f.note += f" | auditor model note: {note.strip()}"
        return vetoed

    # ---- re-verification with an independent template -------------------
    def _verify_affected_count(self, state: CaseState, f: Finding) -> None:
        if f.value.get("basis") != "access_log":
            f.status, f.confidence = "verified", "single_source"
            f.note = (f.note + "; " if f.note else "") + "estimate from ROPA, not a count"
            return
        res = self.inv.q(state, "T04_EXPORT_ITEMS_COUNT", {"incident_id": state.incident_id})
        if not res or not res.rows or not res.rows[0]["n_records"]:
            f.status, f.confidence = "verified", "single_source"
            return
        n_items, n_subj = int(res.rows[0]["n_records"]), int(res.rows[0]["n_subjects"])
        f.evidence.append(evidence_of(res, f"count={n_items},subjects={n_subj}"))
        if n_items == f.value["subjects"]:
            f.status, f.verified_by, f.confidence = "verified", "T04_EXPORT_ITEMS_COUNT", "high"
            f.value["distinct_subjects"] = n_subj
        else:
            f.status, f.confidence = "conflict", "low"
            f.note = f"access log says {f.value['subjects']}, export manifest says {n_items}"
            f.value["subjects"] = max(f.value["subjects"], n_items)

    def _verify_data_categories(self, state: CaseState, f: Finding) -> None:
        res = self.inv.q(state, "T08_EXPORT_CATEGORIES", {"incident_id": state.incident_id})
        if not res or not res.rows:
            f.status, f.confidence = "verified", "single_source"
            return
        exported = sorted({c for r in res.rows for c in r["data_categories"].split(";")})
        f.evidence.append(evidence_of(res, ";".join(exported)))
        if set(exported) <= set(f.value["categories"]):
            f.status, f.verified_by, f.confidence = "verified", "T08_EXPORT_CATEGORIES", "high"
            f.value["exported_categories"] = exported
        else:
            f.status, f.confidence = "conflict", "low"
            f.note = f"exported items carry categories not in ROPA: {sorted(set(exported) - set(f.value['categories']))}"
            f.value["categories"] = sorted(set(f.value["categories"]) | set(exported))

    def _verify_data_unintelligible(self, state: CaseState, f: Finding) -> None:
        res = self.inv.q(state, "T06_ASSET_SECURITY", {"system_id": state.incident["system_id"]})
        if not res or not res.rows:
            f.status, f.confidence = "verified", "single_source"
            return
        a = res.rows[0]
        f.evidence.append(evidence_of(res, a["system_id"]))
        if f.value["basis"] == "attachment_encrypted":
            source_plain = a["encryption_at_rest"] in ("none", "", None)
            if f.value["unintelligible"] and source_plain:
                f.status, f.confidence = "conflict", "low"
                f.note = "log marks the attachment encrypted, but the source system has no encryption at rest"
                f.value["unintelligible"] = False
            else:
                f.status, f.verified_by, f.confidence = "verified", "T06_ASSET_SECURITY", "high"
        else:
            f.status, f.confidence = "verified", "single_source"

    def _verify_disclosure(self, state: CaseState, f: Finding) -> None:
        res = self.inv.q(state, "T02_INCIDENT_UPDATES", {"incident_id": state.incident_id, "as_of": state.as_of})
        codes = [r["code"] for r in (res.rows if res else [])]
        if res:
            f.evidence.append(evidence_of(res, ",".join(r["update_id"] for r in res.rows) or "none"))
        if f.value["left_control"] and "IT_REPORT_NO_EXFIL" in codes and "outbound_transfer" in f.value["actions"]:
            f.status, f.confidence = "conflict", "low"
            f.note = (f"access log shows outbound transfer of {f.value.get('bytes', 0):,} bytes, "
                      "IT report states no evidence of exfiltration")
            return
        if "RECIPIENT_IDENTIFIED" in codes or "RECIPIENT_DELETION_CONFIRMED" in codes:
            f.value["recipient"] = "identified"
        f.status, f.confidence = "verified", "high" if res else "single_source"
        f.verified_by = "T02_INCIDENT_UPDATES" if res else None

    # ---- organisational memory (H-MEM) ----------------------------------
    def reverify_memory(self, state: CaseState, recalled: list[dict], hmem) -> list[dict]:
        from app.orchestration.org_memory import describe, rows_hash
        outcomes = []
        for m in recalled:
            template_id, params_json, expected, _ = m["provenance"]["refs"]
            res = self.inv.q(state, template_id, json.loads(params_json))
            if res is None:
                outcomes.append({"memory": m["id"], "outcome": "source_unavailable"})
                continue
            actual = rows_hash(res.rows)
            ev = evidence_of(res, f"rows_hash={actual}")
            if actual == expected:
                hmem.retrieval.feedback(m["id"], "helpful")
                hmem.store.promote(m["id"], "long_term")
                outcome, note, new_id = "confirmed", "", None
            else:
                hmem.retrieval.feedback(m["id"], "wrong")
                new = hmem.store.add(describe(template_id, res.params, res.rows),
                                     source=f"breach72:{state.run_id}:reverify", type_="factual",
                                     tags=[t for t in m["tags"] if t != "review"], supersedes=m["id"],
                                     provenance={"origin": "user_correction", "learnedAt": hmem.store.clock(),
                                                 "label": f"{state.run_id}/reverify",
                                                 "refs": [template_id, params_json, actual, res.backend]})
                hmem.store.update(m["id"], {"supersededBy": new["id"]}, action="conflict_resolved",
                                  details={"reason": "source differs from memory", "replacement": new["id"]})
                outcome, new_id = "outdated", new["id"]
                note = f"memory {m['id']} was outdated; replaced by {new['id']} from the source"
            state.add(Finding("", f"org_memory:{m['id']}", f"Recalled from organisational memory ({m['id']}, learned in "
                              f"{m['provenance']['label']}) and re-checked at the source: {outcome}",
                              {"memory_id": m["id"], "outcome": outcome, "replacement": new_id,
                               "template_id": template_id},
                              [ev], status="verified", verified_by=template_id,
                              confidence="high" if outcome == "confirmed" else "single_source", note=note),
                      by=self.name)
            self.trace.event(self.name, "memory_reverified", {"memory": m["id"], "template_id": template_id,
                                                              "outcome": outcome, "replacement": new_id})
            outcomes.append({"memory": m["id"], "outcome": outcome, "replacement": new_id})
        return outcomes
