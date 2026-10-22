"""SAMPLE_MODE backend: replays responses recorded from a live Zetaris instance.

Only the data-source answers are replayed. Planning, the rule set, the auditor's
veto and re-verification, and drafting all run for real on every replay.
A recording that was not produced by the live Zetaris adapter is rejected.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from app.adapters.base import QueryResult, SourceError, now_iso, query_hash
from app.adapters.zetaris import RECORDINGS, ZetarisBackend
from app.queries.templates import TEMPLATES


class ZetarisReplayBackend:
    name = "zetaris-replay"

    def __init__(self, recordings: Path = RECORDINGS):
        self.recordings = recordings

    def query(self, template_id: str, params: dict) -> QueryResult:
        qh = query_hash(template_id, params)
        path = self.recordings / f"{qh}.json"
        t0 = time.perf_counter()
        if not path.exists():
            raise SourceError(f"no Zetaris recording for {template_id} {params} (hash {qh})", recoverable=False)
        rec = json.loads(path.read_text(encoding="utf-8"))
        if rec.get("backend") != "zetaris" or rec.get("query_hash") != qh:
            raise SourceError(f"recording {path.name} is not a live Zetaris recording", recoverable=False)
        cols, rows = ZetarisBackend._parse_response(rec["response"])
        return QueryResult(template_id, params, cols, rows, self.name, list(TEMPLATES[template_id].tables), qh,
                           now_iso(), int((time.perf_counter() - t0) * 1000), recorded_at=rec["recorded_at"])
