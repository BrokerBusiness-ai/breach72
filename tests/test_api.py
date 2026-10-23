"""HTTP API: GET /health, POST /run. Dev backend only: no network, no Zetaris, no model calls."""
import json
import threading
import urllib.error
import urllib.request

import pytest

import run


@pytest.fixture()
def api(monkeypatch, tmp_path):
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    monkeypatch.setenv("SAMPLE_MODE", "true")
    server = run.make_server("127.0.0.1", 0, default_backend="dev")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def _call(url, body=None, raw=None):
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def test_health(api):
    code, body = _call(api + "/health")
    assert code == 200 and body == {"status": "ok", "sample_mode": True}


def test_run_case_id(api):
    code, body = _call(api + "/run", {"case_id": "case2_misdirected_email"})
    assert code == 200
    assert body["status"] == "ok"
    assert body["decision"] == "notify_authority_and_individuals"
    assert body["llm_enabled"] is False


def test_run_full_case_object(api):
    case = json.loads((run.ROOT / "input_examples" / "case1_lost_encrypted_laptop.json").read_text(encoding="utf-8"))
    code, body = _call(api + "/run", case)
    assert code == 200 and body["status"] == "ok"
    assert body["decision"] == json.loads(
        (run.ROOT / "output_examples" / "dev" / "case1_lost_encrypted_laptop.json").read_text(encoding="utf-8"))["decision"]


@pytest.mark.parametrize("payload", [{"case_id": "../x"}, {"case_id": "no_such_case"},
                                     {"case_id": "case2_misdirected_email", "backend": "magic"},
                                     {"foo": 1}, [1, 2]])
def test_bad_requests(api, payload):
    code, body = _call(api + "/run", payload)
    assert code == 400
    assert body["type"] == "bad_request" and body["recoverable"] is True


def test_invalid_json(api):
    code, body = _call(api + "/run", raw=b"{not json")
    assert code == 400 and body["type"] == "bad_request"


def test_unknown_path(api):
    code, _ = _call(api + "/nope")
    assert code == 404
