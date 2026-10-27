"""Zetaris SQL over the platform's UI proxy route (local platform, Getting Started repo).

POST {ZETARIS_BASE_URL}/api/proxy/sql-editor/sqls/run-query  body {"sql": ..., "limit": N}
Auth: bearer token from ZETARIS_API_KEY, or from /api/auth/login with
ZETARIS_USERNAME / ZETARIS_PASSWORD. Headers X-Org-ID and a fresh X-Request-ID.

Used by ZetarisBackend when ZETARIS_BASE_URL is set. Credentials come only from
the environment (.env, gitignored) and are never logged or recorded.
"""
from __future__ import annotations

import json
import os
import re
import uuid
import urllib.error
import urllib.request

from app.adapters.base import SourceError

RUN_QUERY = "/api/proxy/sql-editor/sqls/run-query"


class ZetarisProxyClient:
    def __init__(self, opener=None, timeout: float = 20.0):
        self.base_url = os.environ.get("ZETARIS_BASE_URL", "").rstrip("/")
        self.org_id = os.environ.get("ZETARIS_ORG_ID", "")
        self.limit = int(os.environ.get("ZETARIS_QUERY_LIMIT", "1000"))
        self.timeout = timeout
        self._open = opener or urllib.request.urlopen
        self._token = os.environ.get("ZETARIS_API_KEY", "") or None
        if not re.fullmatch(r"https?://[^/]+", self.base_url):
            raise SourceError("ZETARIS_BASE_URL must be a UI origin such as http://localhost:3000", False)
        if not re.fullmatch(r"[0-9]+", self.org_id):
            raise SourceError("ZETARIS_ORG_ID must be the numeric organisation ID", False)

    def _login(self) -> str:
        user, pw = os.environ.get("ZETARIS_USERNAME", ""), os.environ.get("ZETARIS_PASSWORD", "")
        if not user or not pw:
            raise SourceError("set ZETARIS_API_KEY or ZETARIS_USERNAME and ZETARIS_PASSWORD", False)
        req = urllib.request.Request(
            self.base_url + "/api/auth/login",
            data=json.dumps({"username": user, "password": pw}).encode(),
            headers={"Content-Type": "application/json", "X-Request-ID": str(uuid.uuid4())}, method="POST")
        try:
            with self._open(req, timeout=self.timeout) as resp:
                body = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise SourceError(f"Zetaris login failed: HTTP {exc.code}", recoverable=False) from exc
        token = None
        if isinstance(body, dict):
            token = body.get("idToken") or body.get("token") or body.get("accessToken")
        if not token:
            m = re.search(r"[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}", json.dumps(body))
            token = m.group(0) if m else None
        if not token:
            raise SourceError("Zetaris login returned no token", recoverable=False)
        return token

    def request(self, sql: str) -> urllib.request.Request:
        if not self._token:
            self._token = self._login()
        body = {"sql": sql, "limit": self.limit}
        if os.environ.get("ZETARIS_ENGINE_ID"):
            body["engineId"] = os.environ["ZETARIS_ENGINE_ID"]
        headers = {"Content-Type": "application/json", "Accept": "application/json",
                   "Authorization": f"Bearer {self._token}", "X-Org-ID": self.org_id,
                   "X-Request-ID": str(uuid.uuid4())}
        return urllib.request.Request(self.base_url + RUN_QUERY, data=json.dumps(body).encode(),
                                      headers=headers, method="POST")

    def run(self, sql: str) -> dict:
        try:
            with self._open(self.request(sql), timeout=self.timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code == 401 and not os.environ.get("ZETARIS_API_KEY"):
                self._token = None  # expired session token: log in once more
                with self._open(self.request(sql), timeout=self.timeout) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            detail = exc.read(400).decode("utf-8", errors="replace")
            raise SourceError(f"Zetaris HTTP {exc.code}: {detail}",
                              recoverable=exc.code >= 500 or exc.code == 429) from exc
