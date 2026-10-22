"""Common types for data backends."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class SourceError(Exception):
    """A data source failed. `recoverable` tells the orchestrator whether a retry makes sense."""

    def __init__(self, message: str, recoverable: bool):
        super().__init__(message)
        self.recoverable = recoverable


@dataclass
class QueryResult:
    template_id: str
    params: dict
    columns: list[str]
    rows: list[dict]
    backend: str              # "zetaris", "zetaris-replay" or "dev-sqlite"
    tables: list[str]
    query_hash: str
    executed_at: str
    elapsed_ms: int
    recorded_at: str | None = None
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def query_hash(template_id: str, params: dict) -> str:
    blob = json.dumps({"t": template_id, "p": params}, sort_keys=True, ensure_ascii=True)
    return hashlib.sha256(blob.encode()).hexdigest()[:20]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_table_map(backend: str) -> dict[str, str]:
    data = json.loads((ROOT / "config" / "sources.json").read_text(encoding="utf-8"))
    key = "zetaris" if backend.startswith("zetaris") else "dev"
    return data[key]
