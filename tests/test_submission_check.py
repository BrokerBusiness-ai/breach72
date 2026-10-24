import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "submission_check", Path(__file__).resolve().parents[1] / "scripts" / "submission_check.py")
sc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sc)


def test_placeholders_accepted():
    text = "SAMPLE_MODE=true\nZETARIS_API_KEY=your-zetaris-api-key\nZETARIS_ORG_ID=your-org-id\nNVIDIA_API_KEY=\n"
    assert sc.env_placeholders_ok(text) == []


def test_real_looking_key_rejected():
    text = "# comment\nNVIDIA_API_KEY=abc123realvalue\nZETARIS_ORG_ID=acme\n"
    assert sc.env_placeholders_ok(text) == ["NVIDIA_API_KEY", "ZETARIS_ORG_ID"]


def test_secret_patterns():
    assert sc.find_secrets("key = nvapi-" + "A" * 30)
    assert sc.find_secrets("sk-" + "a1" * 12)
    assert sc.find_secrets("ghp_" + "x" * 30)
    assert sc.find_secrets("-----BEGIN RSA " + "PRIVATE KEY-----")  # split so the repo scan stays clean
    assert not sc.find_secrets("NVIDIA_API_KEY=your-nvidia-api-key; risk-score; sk-short")


def test_long_dash_count():
    assert sc.find_long_dashes("a" + chr(0x2013) + "b" + chr(0x2014) + "c") == 2
    assert sc.find_long_dashes("plain - ascii -- hyphens") == 0


def test_read_text_skips_binary(tmp_path):
    binary = tmp_path / "x.bin"
    binary.write_bytes(b"\x00\x01\x02")
    text = tmp_path / "x.txt"
    text.write_text("hello", encoding="utf-8")
    assert sc.read_text(binary) is None
    assert sc.read_text(text) == "hello"


AGENTS = ["Planner", "Investigator", "Evidence Auditor", "Risk Assessor", "Reviewer & Drafter"]


def _result(**over):
    r = {"trace_id": "t-1", "decision": "escalate_to_dpo",
         "agents": ["planner", "investigator", "evidence_auditor", "risk_assessor", "reviewer_drafter"],
         "limitations": ["All data is synthetic (see data/SYNTHETIC.md)."]}
    r.update(over)
    return r


def _write(tmp_path, name, obj):
    import json
    d = tmp_path / "output_examples" / "dev"
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text(json.dumps(obj) if not isinstance(obj, str) else obj, encoding="utf-8")


def test_agent_slug():
    assert sc.agent_slug("Reviewer & Drafter") == "reviewer_drafter"
    assert sc.agent_slug("Evidence Auditor") == "evidence_auditor"


def test_output_examples_missing_is_warn(tmp_path):
    assert sc.check_output_examples(tmp_path, AGENTS)["status"] == "WARN"


def test_output_examples_valid_pass(tmp_path):
    _write(tmp_path, "case1.json", _result())
    _write(tmp_path, "case2.json", _result(decision="notify_authority_only"))
    r = sc.check_output_examples(tmp_path, AGENTS)
    assert r["status"] == "PASS", r


def test_output_examples_malformed_fail(tmp_path):
    _write(tmp_path, "ok.json", _result())
    _write(tmp_path, "no_trace.json", _result(trace_id=""))
    _write(tmp_path, "four_agents.json", _result(agents=["planner", "investigator", "risk_assessor", "reviewer_drafter"]))
    _write(tmp_path, "not_synthetic.json", _result(limitations=["Real data."]))
    _write(tmp_path, "broken.json", "{not json")
    r = sc.check_output_examples(tmp_path, AGENTS)
    assert r["status"] == "FAIL"
    for part in ("no_trace.json: trace_id", "four_agents.json: agents", "not_synthetic.json: synthetic marker", "broken.json: unreadable"):
        assert part in r["reason"], r["reason"]
    assert "ok.json" not in r["reason"]