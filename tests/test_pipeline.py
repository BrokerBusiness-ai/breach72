"""End-to-end and adapter tests. Pipeline tests use the local dev backend (synthetic CSVs)."""
import io
import json
import urllib.error

import pytest

from app.adapters.base import SourceError, query_hash
from app.adapters.zetaris import ZetarisBackend
from app.adapters.zetaris_replay import ZetarisReplayBackend
from app.orchestration.graph import run_case
from app.queries.templates import render, render_inline

CASE2 = {"incident_id": "INC-2026-002", "as_of": "2026-10-20T12:00:00+02:00", "dpo_contact": "iod@x.example"}
CASE2B = dict(CASE2, as_of="2026-10-21T12:00:00+02:00")


@pytest.fixture(autouse=True)
def _no_llm(monkeypatch):
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)


def test_case2_end_to_end_high_risk(tmp_path):
    out = run_case(CASE2, backend="dev", log_dir=tmp_path)
    assert out["status"] == "ok"
    assert out["decision"] == "notify_authority_and_individuals"
    assert out["risk"]["band"] == "high"
    assert out["clock"]["deadline"].startswith("2026-10-23T09:15")
    count = next(f for f in out["findings"] if f["claim"] == "affected_count")
    assert count["value"]["subjects"] == 1200 and count["verified_by"] == "T04_EXPORT_ITEMS_COUNT"
    assert all(f["evidence"] for f in out["findings"])
    assert "Evidence came from the local dev backend, not from Zetaris." in out["limitations"]


def test_case2_branch_recipient_deleted_lowers_risk(tmp_path):
    out = run_case(CASE2B, backend="dev", log_dir=tmp_path)
    assert out["decision"] == "notify_authority"
    assert out["notify_data_subjects"] is False


def test_trace_is_jsonl_and_every_event_has_model_field(tmp_path):
    out = run_case(CASE2, backend="dev", log_dir=tmp_path)
    lines = (tmp_path / f"trace-{out['run_id']}.jsonl").read_text().splitlines()
    events = [json.loads(x) for x in lines]
    assert events[0]["event"] == "start" and events[-1]["event"] == "end"
    assert all("model" in e for e in events)
    assert any(e["event"] == "llm_skipped" and e["model"] is None for e in events)
    assert {e["agent"] for e in events} >= {"planner", "investigator", "evidence_auditor", "risk_assessor",
                                            "reviewer_drafter"}


def test_missing_incident_returns_structured_error(tmp_path):
    out = run_case({"incident_id": "INC-404", "as_of": "2026-10-20T12:00:00+02:00"}, backend="dev",
                   log_dir=tmp_path)
    assert out["status"] == "error" and out["error"]["type"] == "incident_not_found"


def test_conflict_escalates_case3(tmp_path):
    out = run_case({"incident_id": "INC-2026-003", "as_of": "2026-10-21T15:00:00+02:00"}, backend="dev",
                   log_dir=tmp_path)
    assert out["decision"] == "escalate_to_dpo"
    assert "phased_notification" in out["flags"] and "special_categories_art9" in out["flags"]


# ---- templates -----------------------------------------------------------
def test_template_rejects_unexpected_params():
    with pytest.raises(ValueError):
        render("T01_INCIDENT_CORE", {"incident_id": "x", "sql": "DROP"}, {"incidents": "incidents"})


def test_inline_rendering_quotes_strings():
    sql = render_inline("T01_INCIDENT_CORE", {"incident_id": "x' OR '1'='1"}, {"incidents": "incidents"})
    assert sql.endswith("WHERE incident_id = 'x'' OR ''1''=''1'")


# ---- Zetaris adapter and replay -------------------------------------------
class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_zetaris_records_and_replay_reads_it(tmp_path, monkeypatch):
    monkeypatch.setenv("ZETARIS_REST_URL", "http://zetaris.invalid/api/v1.0")
    monkeypatch.setattr("app.adapters.zetaris.RECORDINGS", tmp_path)
    payload = {"columns": ["incident_id", "aware_at"], "rows": [["INC-2026-002", "2026-10-20T09:15:00+02:00"]]}
    be = ZetarisBackend(opener=lambda req, timeout: _Resp(json.dumps(payload).encode()))
    res = be.query("T01_INCIDENT_CORE", {"incident_id": "INC-2026-002"})
    assert res.rows[0]["aware_at"].startswith("2026-10-20")
    rep = ZetarisReplayBackend(recordings=tmp_path).query("T01_INCIDENT_CORE", {"incident_id": "INC-2026-002"})
    assert rep.backend == "zetaris-replay" and rep.rows == res.rows and rep.recorded_at


def test_zetaris_retries_then_fails_recoverable(monkeypatch):
    monkeypatch.setenv("ZETARIS_REST_URL", "http://zetaris.invalid/api/v1.0")
    monkeypatch.setenv("MAX_RETRIES", "2")
    monkeypatch.setattr("app.adapters.zetaris.time.sleep", lambda s: None)
    calls = []

    def opener(req, timeout):
        calls.append(timeout)
        raise urllib.error.URLError("timed out")

    with pytest.raises(SourceError) as e:
        ZetarisBackend(record=False, opener=opener).query("T01_INCIDENT_CORE", {"incident_id": "x"})
    assert e.value.recoverable and len(calls) == 3 and calls[0] == 20.0


def test_replay_rejects_missing_and_foreign_recordings(tmp_path):
    rb = ZetarisReplayBackend(recordings=tmp_path)
    with pytest.raises(SourceError):
        rb.query("T01_INCIDENT_CORE", {"incident_id": "x"})
    qh = query_hash("T01_INCIDENT_CORE", {"incident_id": "x"})
    (tmp_path / f"{qh}.json").write_text(json.dumps({"query_hash": qh, "backend": "dev-sqlite",
                                                     "recorded_at": "t", "response": {"data": []}}))
    with pytest.raises(SourceError):
        rb.query("T01_INCIDENT_CORE", {"incident_id": "x"})
