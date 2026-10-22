"""Append-only JSONL trace. One file per run: logs/trace-<run_id>.jsonl.

Events are written by the code path that performed the action, at the moment it
happened. Every model call carries the `model` field (null when no model was called).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


class Trace:
    def __init__(self, run_id: str, log_dir: Path):
        log_dir.mkdir(parents=True, exist_ok=True)
        self.run_id = run_id
        self.path = log_dir / f"trace-{run_id}.jsonl"
        self.seq = 0

    def event(self, agent: str, event: str, data: dict | None = None, model: str | None = None) -> None:
        self.seq += 1
        rec = {"seq": self.seq, "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
               "run_id": self.run_id, "agent": agent, "event": event, "model": model, "data": data or {}}
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
