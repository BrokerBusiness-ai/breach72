"""Register the four SYNTHETIC source systems in the local Zetaris platform.

Each PostgreSQL database built by scripts/zetaris_sources.py becomes its own Zetaris
datasource (CREATE DATASOURCE + REGISTER DATASOURCE TABLES), so Breach72 reaches
all four systems through one governed SQL access point.

Usage:
    python scripts/zetaris_register.py            # register (skips existing) and verify
    python scripts/zetaris_register.py --sql "SELECT 1"   # run one statement, print JSON

Reads ZETARIS_BASE_URL, ZETARIS_ORG_ID and credentials from .env (gitignored).
The source database password below is a throwaway for synthetic data inside the
local Docker network only.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SOURCES = {
    "incident_db": "SYNTHETIC.incident-log,updates,access-logs,export-items",
    "ropa_db": "SYNTHETIC.record-of-processing-activities",
    "asset_db": "SYNTHETIC.asset-and-encryption-register",
    "contracts_db": "SYNTHETIC.processor-contracts",
}
PG_HOST = os.environ.get("BREACH72_SOURCES_HOST", "breach72-sources")


def load_env() -> None:
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                if v.strip():
                    os.environ.setdefault(k.strip(), v.strip())


def main() -> int:
    load_env()
    from app.adapters.zetaris_proxy import ZetarisProxyClient
    from app.adapters.base import SourceError

    client = ZetarisProxyClient(timeout=120)

    def run(sql: str, quiet_exists: bool = False):
        try:
            return client.run(sql)
        except SourceError as exc:
            if quiet_exists and "exist" in str(exc).lower():
                print("  exists, skipped")
                return None
            raise

    if len(sys.argv) == 3 and sys.argv[1] == "--sql":
        print(json.dumps(run(sys.argv[2]), indent=1, ensure_ascii=False)[:6000])
        return 0

    for name, desc in SOURCES.items():
        print(f"CREATE DATASOURCE {name}")
        run(f'CREATE DATASOURCE {name} DESCRIBE BY "{desc}" OPTIONS ('
            f'jdbcdriver "org.postgresql.Driver", '
            f'jdbcurl "jdbc:postgresql://{PG_HOST}:5432/{name}", '
            f'username "breach72", password "synthetic-only", fetchsize "1000")', quiet_exists=True)
        print(f"REGISTER DATASOURCE TABLES FROM {name}")
        run(f"REGISTER DATASOURCE TABLES FROM {name}", quiet_exists=True)
    print(json.dumps(run("SHOW DATASOURCES"), ensure_ascii=False)[:3000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
