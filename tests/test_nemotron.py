"""Nemotron client: catalogue check, recording, SAMPLE_MODE replay, and the Reviewer's
acceptance gate for model text. The HTTP layer is replaced by a stub opener; no test
produces a decision from model output."""
import io
import json

import pytest

from app.adapters.nemotron import DEFAULTS, NemotronClient, prompt_hash
from app.agents.reviewer import contradicts
from app.orchestration.graph import run_case
from app.orchestration.trace import Trace

CASE2 = {"incident_id": "INC-2026-002", "as_of": "2026-10-20T12:00:00+02:00", "dpo_contact": "iod@x.example"}
CATALOGUE = {"data": [{"id": DEFAULTS["large"]}, {"id": "nvidia/nemotron-3.5-lightning-30b-a3b"}]}


class Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def stub_opener(text, calls):
    def _open(req, timeout=None):
        calls.append((req.get_method(), req.full_url, json.loads(req.data) if req.data else None))
        if req.full_url.endswith("/models"):
            return Resp(json.dumps(CATALOGUE).encode())
        return Resp(json.dumps({"choices": [{"message": {"content": text}}],
                                "usage": {"total_tokens": 42}}).encode())
    return _open


def events(trace):
    return [json.loads(line) for line in trace.path.read_text(encoding="utf-8").splitlines()]


def test_live_call_checks_catalogue_substitutes_retired_model_and_records(tmp_path, monkeypatch):
    monkeypatch.setenv("NVIDIA_API_KEY", "test-key")
    monkeypatch.setenv("MODEL_SMALL", "nvidia/retired-model")
    calls, tr = [], Trace("t1", tmp_path)
    c = NemotronClient(tr, opener=stub_opener("ok [F01]", calls), recordings=tmp_path / "rec")
    assert c.chat("planner", "small", "sys", "user") == "ok [F01]"
    assert calls[0][1].endswith("/models")
    assert calls[1][2]["model"] == "nvidia/nemotron-3.5-lightning-30b-a3b"
    ev = events(tr)
    assert any(e["event"] == "model_substituted" and e["data"]["configured"] == "nvidia/retired-model" for e in ev)
    call = next(e for e in ev if e["event"] == "llm_call")
    assert call["model"] == "nvidia/nemotron-3.5-lightning-30b-a3b"
    rec = json.loads((tmp_path / "rec" / f"{prompt_hash('planner', 'small', 'sys', 'user')}.json").read_text())
    assert rec["backend"] == "nvidia-hosted-api" and rec["model"] == call["model"]


def test_sample_mode_replays_recorded_response_with_its_model(tmp_path, monkeypatch):
    monkeypatch.setenv("NVIDIA_API_KEY", "test-key")
    live = NemotronClient(Trace("rec", tmp_path), opener=stub_opener("recorded [F02]", []),
                          recordings=tmp_path / "rec")
    live.chat("reviewer_drafter", "large", "s", "u")
    monkeypatch.delenv("NVIDIA_API_KEY")
    monkeypatch.setenv("SAMPLE_MODE", "true")
    tr = Trace("rep", tmp_path)
    rep = NemotronClient(tr, opener=lambda *a, **k: pytest.fail("no network in replay"), recordings=tmp_path / "rec")
    assert rep.mode == "replay"
    assert rep.chat("reviewer_drafter", "large", "s", "u") == "recorded [F02]"
    e = next(x for x in events(tr) if x["event"] == "llm_replay")
    assert e["model"] == DEFAULTS["large"] and e["data"]["recorded_at"]
    # a different prompt has no recording: skipped, never generated
    assert rep.chat("reviewer_drafter", "large", "s", "other") is None
    assert events(tr)[-1]["event"] == "llm_skipped" and events(tr)[-1]["model"] is None


def test_replay_rejects_recording_not_from_nvidia(tmp_path, monkeypatch):
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    monkeypatch.setenv("SAMPLE_MODE", "true")
    ph = prompt_hash("a", "large", "s", "u")
    (tmp_path / "rec").mkdir()
    (tmp_path / "rec" / f"{ph}.json").write_text(json.dumps(
        {"prompt_hash": ph, "backend": "handwritten", "model": "x", "recorded_at": "now",
         "response": {"choices": [{"message": {"content": "fake"}}]}}))
    tr = Trace("r", tmp_path)
    assert NemotronClient(tr, recordings=tmp_path / "rec").chat("a", "large", "s", "u") is None
    assert any(e["event"] == "llm_replay_rejected" for e in events(tr))


@pytest.mark.parametrize("decision,text,bad", [
    ("notify_authority_and_individuals", "There is no need to notify anyone [F01].", True),
    ("notify_authority_and_individuals", "The data subjects must be informed [F01].", False),
    ("notify_authority", "The controller does not need to notify the supervisory authority [F01].", True),
    ("notify_authority", "The authority is notified; data subjects are not informed [F01].", False),
    ("document_internally", "The breach must be notified to UODO [F01].", True),
    ("document_internally", "Notification of the supervisory authority is required [F01].", True),
    ("document_internally", "The breach is documented internally under Art. 33(5) [F01].", False),
])
def test_contradiction_gate(decision, text, bad):
    assert (contradicts(decision, text) is not None) is bad


def _llm_returns(monkeypatch, tmp_path, text):
    monkeypatch.setenv("NVIDIA_API_KEY", "test-key")
    import app.adapters.nemotron as nm
    monkeypatch.setattr(nm, "RECORDINGS", tmp_path / "rec")
    orig = nm.NemotronClient.__init__

    def init(self, trace=None, opener=None, recordings=None):
        orig(self, trace, opener=stub_opener(text, []), recordings=recordings)
    monkeypatch.setattr(nm.NemotronClient, "__init__", init)


def test_pipeline_accepts_cited_summary_but_decision_unchanged(tmp_path, monkeypatch):
    _llm_returns(monkeypatch, tmp_path, "Export of 1,200 records reached an outside recipient [F01].")
    out = run_case(CASE2, backend="dev", log_dir=tmp_path)
    assert out["decision"] == "notify_authority_and_individuals"
    assert out["model_summary"] and "[F01]" in out["model_summary"]


def test_pipeline_rejects_summary_that_contradicts_decision(tmp_path, monkeypatch):
    _llm_returns(monkeypatch, tmp_path, "No notification is needed here [F01].")
    out = run_case(CASE2, backend="dev", log_dir=tmp_path)
    assert out["decision"] == "notify_authority_and_individuals"
    assert not out["model_summary"]
    tr = next(tmp_path.glob("trace-*.jsonl")).read_text(encoding="utf-8")
    assert "contradicts the decision" in tr


def test_pipeline_rejects_summary_with_unknown_finding_id(tmp_path, monkeypatch):
    _llm_returns(monkeypatch, tmp_path, "Everything is fine [F99].")
    out = run_case(CASE2, backend="dev", log_dir=tmp_path)
    assert not out["model_summary"]


def test_prompt_hash_ignores_per_run_identifiers():
    a = "F03 x <fact_66cc444b52b7dcd6; auditor> learned in 20261023T095313-1ce734/F02"
    b = "F03 x <fact_a0c73a2b6f40067b; auditor> learned in 20261024T101500-abc123/F02"
    assert prompt_hash("r", "large", "s", a) == prompt_hash("r", "large", "s", b)
    assert prompt_hash("r", "large", "s", a) != prompt_hash("r", "large", "s", a.replace("F03", "F04"))
