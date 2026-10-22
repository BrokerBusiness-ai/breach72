"""Local development backend: the synthetic CSVs loaded into in-memory SQLite.

This is NOT Zetaris. It exists so that the rule set and agents can be developed
and unit-tested offline. Results are labelled backend="dev-sqlite" in every
finding and trace event, and they are never written to recordings/, so they can
never be replayed as if they came from Zetaris.
"""
from __future__ import annotations

import csv
import sqlite3
import time
from pathlib import Path

from app.adapters.base import QueryResult, ROOT, SourceError, load_table_map, now_iso, query_hash
from app.queries.templates import TEMPLATES, render

INT_COLS = {"bytes", "records_count", "case_no", "subjects_estimate", "breach_notice_hours"}


class DevSQLiteBackend:
    name = "dev-sqlite"

    def __init__(self, data_dir: Path | None = None):
        self.data_dir = data_dir or ROOT / "data" / "synthetic"
        if not (self.data_dir / "incidents.csv").exists():
            from data.generator import write_all
            write_all(self.data_dir)
        self.table_map = load_table_map("dev")
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        for logical in self.table_map.values():
            self._load(logical)

    def _load(self, table: str) -> None:
        with open(self.data_dir / f"{table}.csv", encoding="utf-8") as fh:
            reader = csv.reader(fh)
            cols = next(reader)
            types = ["INTEGER" if c in INT_COLS else "TEXT" for c in cols]
            self.conn.execute(f"CREATE TABLE {table} (" + ", ".join(f"{c} {t}" for c, t in zip(cols, types)) + ")")
            rows = [[(int(v) if t == "INTEGER" and v != "" else (None if v == "" else v)) for v, t in zip(r, types)]
                    for r in reader]
            self.conn.executemany(f"INSERT INTO {table} VALUES ({','.join('?' * len(cols))})", rows)

    def query(self, template_id: str, params: dict) -> QueryResult:
        sql, values = render(template_id, params, self.table_map)
        t0 = time.perf_counter()
        try:
            cur = self.conn.execute(sql, values)
        except sqlite3.Error as exc:
            raise SourceError(f"dev-sqlite: {exc}", recoverable=False) from exc
        rows = [dict(r) for r in cur.fetchall()]
        cols = [d[0] for d in cur.description] if cur.description else []
        return QueryResult(template_id, params, cols, rows, self.name, list(TEMPLATES[template_id].tables),
                           query_hash(template_id, params), now_iso(), int((time.perf_counter() - t0) * 1000))
