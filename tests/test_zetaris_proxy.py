"""The local Zetaris platform route: UI proxy login, SQL run, response parsing."""
import json

import pytest

from app.adapters.base import SourceError
from app.adapters.zetaris import ZetarisBackend


class _Resp:
    def __init__(self, body: bytes):
        self.body = body

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _platform_env(monkeypatch):
    monkeypatch.setenv("ZETARIS_BASE_URL", "http://localhost:3000")
    monkeypatch.setenv("ZETARIS_ORG_ID", "1")
    monkeypatch.setenv("ZETARIS_USERNAME", "user@example.org")
    monkeypatch.setenv("ZETARIS_PASSWORD", "not-a-real-password")
    monkeypatch.delenv("ZETARIS_API_KEY", raising=False)
    monkeypatch.delenv("ZETARIS_REST_URL", raising=False)


def test_proxy_logs_in_runs_sql_and_parses_headers_data(tmp_path, monkeypatch):
    _platform_env(monkeypatch)
    monkeypatch.setattr("app.adapters.zetaris.RECORDINGS", tmp_path)
    calls = []

    def opener(req, timeout):
        calls.append((req.full_url, dict(req.header_items()), json.loads(req.data)))
        if req.full_url.endswith("/api/auth/login"):
            return _Resp(json.dumps({"idToken": "aaaaaaaa.bbbbbbbb.cccccccc"}).encode())
        return _Resp(json.dumps({"headers": ["log_id", "bytes", "destination"],
                                 "data": [["LOG-0004", "2147483648", "198.51.100.23"],
                                          ["LOG-0001", "0", None]], "total": 2}).encode())

    be = ZetarisBackend(opener=opener)
    res = be.query("T01_INCIDENT_CORE", {"incident_id": "INC-2026-003"})
    login, run = calls
    assert login[0] == "http://localhost:3000/api/auth/login"
    assert run[0] == "http://localhost:3000/api/proxy/sql-editor/sqls/run-query"
    headers = {k.lower(): v for k, v in run[1].items()}
    assert headers["authorization"] == "Bearer aaaaaaaa.bbbbbbbb.cccccccc" and headers["x-org-id"] == "1"
    assert "incident_db.incidents" in run[2]["sql"]
    assert res.rows[0]["bytes"] == 2147483648 and res.rows[1]["destination"] is None
    rec = json.loads(next(tmp_path.glob("*.json")).read_text(encoding="utf-8"))
    assert "not-a-real-password" not in json.dumps(rec) and "aaaaaaaa" not in json.dumps(rec)


def test_proxy_rejects_non_numeric_org(monkeypatch):
    _platform_env(monkeypatch)
    monkeypatch.setenv("ZETARIS_ORG_ID", "your-org-id")
    with pytest.raises(SourceError):
        ZetarisBackend(opener=lambda req, timeout: None)
