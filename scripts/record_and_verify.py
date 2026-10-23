"""Record a full live run on Zetaris + Nemotron, then prove SAMPLE_MODE replays it.

Usage (after Zetaris is up and .env holds real values):
    python scripts/record_and_verify.py

Step 1: python run.py --all --backend zetaris --out-dir output_examples/zetaris_nemotron
        (every Zetaris and Nemotron response is recorded under recordings/)
Step 2: the same six cases in SAMPLE_MODE with an empty environment
        (no Zetaris URL, no NVIDIA key), as a judge would run them from a clean clone
Step 3: field-by-field comparison; writes docs/replay_check.json; exit 0 only on a full match.

Replay never invents a verdict: a missing recording is an error, not a default answer.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LIVE_DIR = ROOT / "output_examples" / "zetaris_nemotron"
REPORT = ROOT / "docs" / "replay_check.json"

# Fields that must be identical between the live run and the replay.
COMPARED = ("status", "decision", "notify_supervisory_authority", "notify_data_subjects",
            "escalation_reasons", "risk", "model_summary", "vetoes")

# Variables emptied for the replay so run.load_env() (setdefault) cannot fill them from .env.
EMPTIED = ("ZETARIS_REST_URL", "ZETARIS_API_KEY", "ZETARIS_ORG_ID", "NVIDIA_API_KEY")


def replay_env(base: dict | None = None) -> dict:
    env = dict(os.environ if base is None else base)
    for k in EMPTIED:
        env[k] = ""
    env["SAMPLE_MODE"] = "true"
    return env


def compare_results(live: dict, replay: dict, fields=COMPARED) -> list[dict]:
    """Return one entry per differing field; an empty list means the replay matches."""
    diffs = []
    for f in fields:
        a, b = live.get(f), replay.get(f)
        if json.dumps(a, sort_keys=True, default=str) != json.dumps(b, sort_keys=True, default=str):
            diffs.append({"field": f, "live": a, "replay": b})
    return diffs


def compare_dirs(live_dir: Path, replay_dir: Path) -> dict:
    cases = {}
    for p in sorted(live_dir.glob("*.json")):
        r = replay_dir / p.name
        if not r.exists():
            cases[p.stem] = {"match": False, "diffs": [{"field": "file", "live": "present", "replay": "missing"}]}
            continue
        diffs = compare_results(json.loads(p.read_text(encoding="utf-8")),
                                json.loads(r.read_text(encoding="utf-8")))
        cases[p.stem] = {"match": not diffs, "diffs": diffs}
    return {"cases": cases, "all_match": bool(cases) and all(c["match"] for c in cases.values())}


def _run(args: list[str], env: dict) -> int:
    print("$", " ".join(args), flush=True)
    return subprocess.call([sys.executable, *args], cwd=ROOT, env=env)


def main() -> int:
    live_env = dict(os.environ)
    live_env["SAMPLE_MODE"] = "false"
    if _run(["run.py", "--all", "--backend", "zetaris", "--out-dir", str(LIVE_DIR)], live_env) != 0:
        print("live run did not finish with status ok for every case; fix before recording", file=sys.stderr)
        return 2
    with tempfile.TemporaryDirectory(prefix="breach72-replay-") as tmp:
        if _run(["run.py", "--all", "--backend", "replay", "--out-dir", tmp], replay_env()) != 0:
            print("replay failed: a recording is missing or stale", file=sys.stderr)
        result = compare_dirs(LIVE_DIR, Path(tmp))
    REPORT.parent.mkdir(exist_ok=True)
    REPORT.write_text(json.dumps(result, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    for name, c in result["cases"].items():
        print(f"{name}: {'match' if c['match'] else 'DIFF ' + ', '.join(d['field'] for d in c['diffs'])}")
    return 0 if result["all_match"] else 1


if __name__ == "__main__":
    sys.exit(main())