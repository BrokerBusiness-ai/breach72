"""Zetaris adapter: one governed SQL access point to the four source systems.

- Executes only vetted templates (app/queries/templates.py).
- Timeout per call (SOURCE_TIMEOUT_SECONDS, default 20 s) and a bounded number of
  retries (MAX_RETRIES, default 2) with backoff, for recoverable errors only.
- Every successful response is recorded verbatim to recordings/<query_hash>.json
  so that SAMPLE_MODE can replay real Zetaris answers for judges without credentials.

The request and response shapes are isolated in `_build_request` and
`_parse_response`. They follow the REST endpoint of the local Zetaris platform
(http://localhost:8888/api/v1.0) and must be confirmed against the Participant
Setup Guide once verify.sh passes.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

from app.adapters.base import QueryResult, ROOT, SourceError, load_table_map, now_iso, query_hash
from app.queries.templates import TEMPLATES, render_inline

RECORDINGS = ROOT / "recordings"


INT_COLS = {"bytes", "records_count", "case_no", "subjects_estimate", "breach_notice_hours"}


def _coerce(col: str, value):
    """Integer columns come back as strings from the platform; restore the source type."""
    if col in INT_COLS and isinstance(value, str) and value.lstrip("-").isdigit():
        return int(value)
    if value in ("null", "NULL"):
        return None
    return value


class ZetarisBackend:
    name = "zetaris"

    def __init__(self, record: bool = True, opener=None):
        self.base_url = os.environ.get("ZETARIS_REST_URL", "").rstrip("/")
        self.query_path = os.environ.get("ZETARIS_QUERY_PATH", "/query")
        self.api_key = os.environ.get("ZETARIS_API_KEY", "")
        self.org_id = os.environ.get("ZETARIS_ORG_ID", "")
        self.timeout = float(os.environ.get("SOURCE_TIMEOUT_SECONDS", "20"))
        self.max_retries = int(os.environ.get("MAX_RETRIES", "2"))
        self.record = record
        self.table_map = load_table_map("zetaris")
        self._open = opener or urllib.request.urlopen
        self.proxy = None
        if os.environ.get("ZETARIS_BASE_URL"):
            from app.adapters.zetaris_proxy import ZetarisProxyClient
            self.proxy = ZetarisProxyClient(opener=opener, timeout=self.timeout)
            self.base_url = self.proxy.base_url
        if not self.base_url:
            raise SourceError("ZETARIS_REST_URL is not set (use SAMPLE_MODE=true to replay recordings)", False)

    def _build_request(self, sql: str) -> urllib.request.Request:
        body = json.dumps({"query": sql}).encode()
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        if self.org_id:
            headers["X-Org-Id"] = self.org_id
        return urllib.request.Request(self.base_url + self.query_path, data=body, headers=headers, method="POST")

    @staticmethod
    def _parse_response(payload: dict) -> tuple[list[str], list[dict]]:
        # Accept {"headers": [...], "data": [[...]]} (local platform, values as strings),
        # {"columns": [...], "rows": [[...]]} or {"data": [{...}]}
        if "headers" in payload and isinstance(payload.get("data"), list):
            cols = [c["name"] if isinstance(c, dict) else c for c in payload["headers"]]
            rows = [dict(zip(cols, r)) if isinstance(r, list) else r for r in payload["data"]]
            return cols, [{k: _coerce(k, v) for k, v in r.items()} for r in rows]
        if "columns" in payload and "rows" in payload:
            cols = [c["name"] if isinstance(c, dict) else c for c in payload["columns"]]
            return cols, [dict(zip(cols, r)) if isinstance(r, list) else r for r in payload["rows"]]
        if "data" in payload and isinstance(payload["data"], list):
            rows = payload["data"]
            return (list(rows[0].keys()) if rows else []), rows
        raise SourceError(f"unexpected Zetaris response keys: {sorted(payload)[:5]}", recoverable=False)

    def query(self, template_id: str, params: dict) -> QueryResult:
        sql = render_inline(template_id, params, self.table_map)
        attempt, last = 0, None
        while attempt <= self.max_retries:
            t0 = time.perf_counter()
            try:
                if self.proxy is not None:
                    payload = self.proxy.run(sql)
                else:
                    with self._open(self._build_request(sql), timeout=self.timeout) as resp:
                        payload = json.loads(resp.read().decode("utf-8"))
                cols, rows = self._parse_response(payload)
                qh = query_hash(template_id, params)
                res = QueryResult(template_id, params, cols, rows, self.name, list(TEMPLATES[template_id].tables),
                                  qh, now_iso(), int((time.perf_counter() - t0) * 1000),
                                  extra={"attempts": attempt + 1})
                if self.record:
                    self._save(res, sql, payload)
                return res
            except SourceError as exc:
                last = exc
            except urllib.error.HTTPError as exc:
                last = SourceError(f"Zetaris HTTP {exc.code}", recoverable=exc.code >= 500 or exc.code == 429)
            except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
                last = SourceError(f"Zetaris unreachable or timed out: {exc}", recoverable=True)
            if not last.recoverable:
                raise last
            attempt += 1
            if attempt <= self.max_retries:
                time.sleep(min(2 ** attempt, 8))
        raise last

    def _save(self, res: QueryResult, sql: str, payload: dict) -> None:
        RECORDINGS.mkdir(exist_ok=True)
        rec = {"query_hash": res.query_hash, "template_id": res.template_id, "params": res.params, "sql": sql,
               "recorded_at": res.executed_at, "backend": "zetaris", "response": payload}
        (RECORDINGS / f"{res.query_hash}.json").write_text(json.dumps(rec, indent=1), encoding="utf-8")
