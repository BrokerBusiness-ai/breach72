"""Offline tests for scripts/record_and_verify.py (no network, no model)."""
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("rv", ROOT / "scripts" / "record_and_verify.py")
rv = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rv)

BASE = {"status": "ok", "decision": "notify_sa_and_subjects", "notify_supervisory_authority": True,
        "notify_data_subjects": True, "escalation_reasons": [], "risk": {"score": 3.5},
        "model_summary": "SYNTHETIC summary", "vetoes": [], "run_id": "a", "execution_time": 1.2}


def test_identical_results_match_ignoring_run_metadata():
    other = dict(BASE, run_id="b", execution_time=0.01)
    assert rv.compare_results(BASE, other) == []


def test_changed_decision_is_reported():
    diffs = rv.compare_results(BASE, dict(BASE, decision="document_only"))
    assert [d["field"] for d in diffs] == ["decision"]


def test_nested_risk_difference_is_reported():
    diffs = rv.compare_results(BASE, dict(BASE, risk={"score": 2.5}))
    assert [d["field"] for d in diffs] == ["risk"]


def test_replay_env_empties_secrets_and_forces_sample_mode():
    env = rv.replay_env({"NVIDIA_API_KEY": "x", "ZETARIS_REST_URL": "http://h", "PATH": "p"})
    assert env["NVIDIA_API_KEY"] == "" and env["ZETARIS_REST_URL"] == ""
    assert env["SAMPLE_MODE"] == "true" and env["PATH"] == "p"


def test_compare_dirs_flags_missing_replay(tmp_path):
    live, rep = tmp_path / "live", tmp_path / "rep"
    live.mkdir(); rep.mkdir()
    (live / "case1.json").write_text(json.dumps(BASE), encoding="utf-8")
    (live / "case2.json").write_text(json.dumps(BASE), encoding="utf-8")
    (rep / "case1.json").write_text(json.dumps(BASE), encoding="utf-8")
    out = rv.compare_dirs(live, rep)
    assert out["cases"]["case1"]["match"] is True
    assert out["cases"]["case2"]["match"] is False
    assert out["all_match"] is False


def test_compare_dirs_empty_is_not_a_match(tmp_path):
    (tmp_path / "a").mkdir(); (tmp_path / "b").mkdir()
    assert rv.compare_dirs(tmp_path / "a", tmp_path / "b")["all_match"] is False