"""Submission self-check for Breach72.

Audits the repository against the Open Agent Hackathon 2026 submission rules
before anything is submitted. Standard library only, no network.

Usage:
    python scripts/submission_check.py          # one line per check, exit 0 if no FAIL
    python scripts/submission_check.py --json   # also writes docs/submission_check.json
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

REQUIRED_FILES = [
    "README.md", "LICENSE", "metadata.json", ".env.example", "Dockerfile",
    "data/SYNTHETIC.md", "docs/Breach72_deck.pdf",
]
README_HEADINGS = ["Quick start", "AI and Model Usage", "Third-Party Code and Licences", "Limitations"]
METADATA_KEYS = ["project_name", "track", "team", "repository", "license", "agents", "sponsor_technologies"]
SPONSORS = ["zetaris", "meterless", "nvidia", "cursor"]
SECRET_PATTERNS = [
    re.compile(r"nvapi-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    re.compile(r"ghp_[A-Za-z0-9]{20,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
]
LONG_DASHES = (chr(0x2013), chr(0x2014))
SELF = "scripts/submission_check.py"


def check(name: str, status: str, reason: str) -> dict:
    return {"check": name, "status": status, "reason": reason}


def env_placeholders_ok(text: str) -> list[str]:
    """Return keys in an .env.example text whose secret-like value is not a placeholder."""
    bad = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = (part.strip() for part in line.split("=", 1))
        if (key.endswith("_KEY") or key.endswith("_ORG_ID")) and value and not value.startswith("your-"):
            bad.append(key)
    return bad


def find_secrets(text: str) -> list[str]:
    return [p.pattern for p in SECRET_PATTERNS if p.search(text)]


def find_long_dashes(text: str) -> int:
    return sum(text.count(d) for d in LONG_DASHES)


def tracked_files(root: Path) -> list[Path]:
    out = subprocess.run(["git", "ls-files"], cwd=root, capture_output=True, text=True, check=True)
    return [root / p for p in out.stdout.splitlines() if p]


def read_text(path: Path) -> str | None:
    try:
        data = path.read_bytes()
    except OSError:
        return None
    if b"\0" in data[:4096]:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def run_checks(root: Path = ROOT) -> list[dict]:
    results = []

    missing = [f for f in REQUIRED_FILES if not (root / f).is_file()]
    results.append(check("required_files", "FAIL" if missing else "PASS",
                         f"missing: {', '.join(missing)}" if missing else f"{len(REQUIRED_FILES)} present"))

    readme = (root / "README.md").read_text(encoding="utf-8") if (root / "README.md").is_file() else ""
    headings = {m.group(1).strip() for m in re.finditer(r"^#{1,6}\s+(.+)$", readme, re.M)}
    absent = [h for h in README_HEADINGS if not any(h in x for x in headings)]
    results.append(check("readme_sections", "FAIL" if absent else "PASS",
                         f"missing: {', '.join(absent)}" if absent else "all required sections"))

    try:
        meta = json.loads((root / "metadata.json").read_text(encoding="utf-8"))
        problems = [k for k in METADATA_KEYS if k not in meta]
        if len(meta.get("agents", [])) != 5:
            problems.append("agents != 5")
        problems += [f"sponsor {s}" for s in SPONSORS if s not in meta.get("sponsor_technologies", {})]
        results.append(check("metadata_json", "FAIL" if problems else "PASS",
                             "; ".join(problems) if problems else "keys, 5 agents, 4 sponsors"))
    except (OSError, ValueError) as exc:
        results.append(check("metadata_json", "FAIL", f"unreadable: {exc.__class__.__name__}"))

    gitignore = (root / ".gitignore").read_text(encoding="utf-8") if (root / ".gitignore").is_file() else ""
    gi_lines = {l.strip() for l in gitignore.splitlines()}
    env_example = (root / ".env.example").read_text(encoding="utf-8") if (root / ".env.example").is_file() else ""
    bad_keys = env_placeholders_ok(env_example)
    gi_ok = ".env" in gi_lines and "!.env.example" in gi_lines
    status = "PASS" if gi_ok and not bad_keys else "FAIL"
    reason = "'.env' ignored, '.env.example' kept, placeholders only"
    if not gi_ok:
        reason = ".gitignore must ignore .env and keep .env.example"
    if bad_keys:
        reason = f"non-placeholder values: {', '.join(bad_keys)}"
    results.append(check("env_hygiene", status, reason))

    files = tracked_files(root)
    if any(f.relative_to(root).as_posix() == ".env" for f in files):
        results.append(check("env_not_tracked", "FAIL", ".env is tracked by git"))
    else:
        results.append(check("env_not_tracked", "PASS", ".env not tracked"))

    secret_hits, dash_hits, scanned = [], [], 0
    for f in files:
        rel = f.relative_to(root).as_posix()
        if rel == SELF:
            continue
        text = read_text(f)
        if text is None:
            continue
        scanned += 1
        if find_secrets(text):
            secret_hits.append(rel)
        if find_long_dashes(text):
            dash_hits.append(rel)
    results.append(check("secret_scan", "FAIL" if secret_hits else "PASS",
                         f"patterns in: {', '.join(secret_hits)}" if secret_hits else f"{scanned} tracked text files clean"))
    results.append(check("dash_scan", "FAIL" if dash_hits else "PASS",
                         f"U+2013/U+2014 in: {', '.join(dash_hits)}" if dash_hits else "no U+2013/U+2014"))

    zetaris_recs = list((root / "recordings").glob("*.json"))
    results.append(check("zetaris_recordings", "PASS" if zetaris_recs else "WARN",
                         f"{len(zetaris_recs)} recorded Zetaris responses" if zetaris_recs else
                         "sample mode cannot replay until a live Zetaris run is recorded"))

    leaks = []
    for folder in ("recordings", "output_examples"):
        for f in (root / folder).rglob("*"):
            if f.is_file():
                text = read_text(f) or ""
                if "reasoning_content" in text or find_secrets(text):
                    leaks.append(f.relative_to(root).as_posix())
    results.append(check("recordings_clean", "FAIL" if leaks else "PASS",
                         f"reasoning or secret in: {', '.join(leaks[:5])}" if leaks else "no reasoning_content, no keys"))

    cursor = list((root / "docs" / "cursor_usage").glob("*.md"))
    results.append(check("cursor_evidence", "PASS" if len(cursor) >= 3 else "FAIL",
                         f"{len(cursor)} documented Cursor sessions"))

    cases = list((root / "input_examples").glob("*.json"))
    results.append(check("test_cases", "PASS" if len(cases) >= 5 else "FAIL", f"{len(cases)} input examples"))

    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--json", action="store_true", help="write docs/submission_check.json")
    args = parser.parse_args(argv)
    results = run_checks()
    for r in results:
        print(f"{r['status']:<4}  {r['check']:<20} {r['reason']}")
    failed = sum(r["status"] == "FAIL" for r in results)
    warned = sum(r["status"] == "WARN" for r in results)
    print(f"\n{len(results)} checks, {failed} FAIL, {warned} WARN")
    if args.json:
        (ROOT / "docs" / "submission_check.json").write_text(
            json.dumps({"results": results, "fail": failed, "warn": warned}, indent=2) + "\n", encoding="utf-8")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
