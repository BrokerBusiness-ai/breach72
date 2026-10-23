"""Breach72 command line.

    python run.py input_examples/case2_misdirected_email.json            # SAMPLE_MODE replay or live Zetaris
    python run.py input_examples/case2_misdirected_email.json --backend dev
    python run.py input_examples/case2_misdirected_email.json --out output_examples/case2.json
    python run.py --all --backend dev --out-dir output_examples
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

# Fixed order for --all: organisational memory is empty on the first case, then shared.
ALL_CASES = (
    "case2_misdirected_email",
    "case2b_recipient_deleted",
    "case1_lost_encrypted_laptop",
    "case3_ransomware_hr",
    "case4_processor_late_notice",
    "case5_no_detection_date",
)


def load_env(path: Path = ROOT / ".env") -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


def _run_one(case_path: Path, backend: str | None) -> dict:
    from app.orchestration.graph import run_case
    case = json.loads(case_path.read_text(encoding="utf-8"))
    return run_case(case, backend)


def _summary_line(result: dict) -> str:
    return f"{result.get('incident_id')} {result.get('decision')} {result.get('status')}"


def main(argv=None) -> int:
    load_env()
    ap = argparse.ArgumentParser(description="Breach72: GDPR Art. 33/34 breach investigation agents")
    ap.add_argument("case", nargs="?", type=Path, default=None,
                    help="path to a single case JSON (required unless --all)")
    ap.add_argument("--all", action="store_true",
                    help="run all input_examples cases in the fixed demo order")
    ap.add_argument("--backend", choices=["zetaris", "replay", "dev"], default=None)
    ap.add_argument("--out", type=Path, default=None, help="write single-case JSON here")
    ap.add_argument("--out-dir", type=Path, default=None,
                    help="with --all: write one JSON per case named after the input file")
    ap.add_argument("--keep-memory", action="store_true",
                    help="with --all: keep existing HMEM_DIR instead of a fresh empty temp dir")
    a = ap.parse_args(argv)
    if not a.all and a.case is None:
        ap.error("provide a case path or --all")

    if a.all:
        return _run_all(a.backend, a.out_dir, a.keep_memory)

    result = _run_one(a.case, a.backend)
    text = json.dumps(result, indent=2, ensure_ascii=False, default=str)
    if a.out:
        a.out.parent.mkdir(parents=True, exist_ok=True)
        a.out.write_text(text, encoding="utf-8")
    print(text)
    return 0 if result["status"] == "ok" else 2


def _run_all(backend: str | None, out_dir: Path | None, keep_memory: bool) -> int:
    if out_dir is not None:
        out_dir.mkdir(parents=True, exist_ok=True)

    def run_cases() -> int:
        ok = True
        for name in ALL_CASES:
            path = ROOT / "input_examples" / f"{name}.json"
            result = _run_one(path, backend)
            print(_summary_line(result))
            if result.get("status") != "ok":
                ok = False
            if out_dir is not None:
                text = json.dumps(result, indent=2, ensure_ascii=False, default=str)
                (out_dir / f"{name}.json").write_text(text, encoding="utf-8")
        return 0 if ok else 2

    if keep_memory:
        return run_cases()
    with tempfile.TemporaryDirectory(prefix="breach72-hmem-") as hmem:
        os.environ["HMEM_DIR"] = hmem
        return run_cases()


if __name__ == "__main__":
    raise SystemExit(main())
