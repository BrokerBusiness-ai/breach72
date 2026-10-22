"""Breach72 command line.

    python run.py input_examples/case2_misdirected_email.json            # SAMPLE_MODE replay or live Zetaris
    python run.py input_examples/case2_misdirected_email.json --backend dev
    python run.py input_examples/case2_misdirected_email.json --out output_examples/case2.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))


def load_env(path: Path = ROOT / ".env") -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


def main(argv=None) -> int:
    load_env()
    from app.orchestration.graph import run_case
    ap = argparse.ArgumentParser(description="Breach72: GDPR Art. 33/34 breach investigation agents")
    ap.add_argument("case", type=Path)
    ap.add_argument("--backend", choices=["zetaris", "replay", "dev"], default=None)
    ap.add_argument("--out", type=Path, default=None)
    a = ap.parse_args(argv)
    case = json.loads(a.case.read_text(encoding="utf-8"))
    result = run_case(case, a.backend)
    text = json.dumps(result, indent=2, ensure_ascii=False, default=str)
    if a.out:
        a.out.parent.mkdir(parents=True, exist_ok=True)
        a.out.write_text(text, encoding="utf-8")
    print(text)
    return 0 if result["status"] == "ok" else 2


if __name__ == "__main__":
    raise SystemExit(main())
