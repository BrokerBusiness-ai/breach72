"""Live demo on Zetaris for the video: every line is printed as it happens and
also written with its time offset to a cast file (logs/demo_cast.jsonl), so the
video shows the real run at its real pace.

    python scripts/demo_live.py            # needs the local Zetaris platform and .env

Steps: the four datasources Zetaris federates; one federated query across all
four source systems; then case 3 (ransomware, conflicting sources) end to end,
streaming the agent trace while it runs.
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
CAST = ROOT / "logs" / "demo_cast.jsonl"
T0 = time.perf_counter()
_cast = None


def out(text: str = "", style: str = "plain") -> None:
    print(text, flush=True)
    _cast.write(json.dumps({"t": round(time.perf_counter() - T0, 3), "text": text, "style": style}) + "\n")
    _cast.flush()


def table(headers, rows, widths=None):
    widths = widths or [max(len(str(h)), *(len(str(r[i])) for r in rows)) for i, h in enumerate(headers)]
    out("  " + "  ".join(str(h).ljust(w) for h, w in zip(headers, widths)), "head")
    for r in rows:
        out("  " + "  ".join(str(v if v is not None else "null").ljust(w)[:w] for v, w in zip(r, widths)), "row")


def main() -> int:
    global _cast
    CAST.parent.mkdir(exist_ok=True)
    _cast = open(CAST, "w", encoding="utf-8")
    from scripts.zetaris_register import load_env
    load_env()
    from app.adapters.zetaris_proxy import ZetarisProxyClient
    z = ZetarisProxyClient(timeout=60)

    out("$ zetaris> SHOW DATASOURCES", "cmd")
    t = time.perf_counter()
    r = z.run("SHOW DATASOURCES")
    table(["name", "description"], [[d[0], d[1]] for d in r["data"]])
    out(f"  {r['total']} datasources, {int((time.perf_counter() - t) * 1000)} ms", "dim")
    out()

    sql = ("SELECT i.incident_id, a.encryption_at_rest, r.data_categories, r.special_categories, "
           "c.breach_notice_hours "
           "FROM incident_db.incidents i "
           "JOIN asset_db.assets a ON a.system_id = i.system_id "
           "JOIN ropa_db.ropa r ON r.system_id = i.system_id "
           "LEFT JOIN contracts_db.processor_contracts c ON c.processor_id = r.processor_id "
           "WHERE i.incident_id = 'INC-2026-003'")
    out("$ zetaris> -- one question, four systems", "cmd")
    for part in ["SELECT i.incident_id, a.encryption_at_rest, r.data_categories,",
                 "       r.special_categories, c.breach_notice_hours",
                 "FROM incident_db.incidents i",
                 "JOIN asset_db.assets a            ON a.system_id = i.system_id",
                 "JOIN ropa_db.ropa r               ON r.system_id = i.system_id",
                 "LEFT JOIN contracts_db.processor_contracts c ON c.processor_id = r.processor_id",
                 "WHERE i.incident_id = 'INC-2026-003'"]:
        out("  " + part, "sql")
    t = time.perf_counter()
    r = z.run(sql)
    table(r["headers"], r["data"])
    out(f"  {r['total']} row from 4 source systems, {int((time.perf_counter() - t) * 1000)} ms", "dim")
    out()

    out("$ python run.py input_examples/case3_ransomware_hr.json --backend zetaris", "cmd")
    before = {p.name for p in (ROOT / "logs").glob("trace-*.jsonl")}
    proc = subprocess.Popen([sys.executable, "run.py", "input_examples/case3_ransomware_hr.json",
                             "--backend", "zetaris", "--out", "logs/demo_case3.json"],
                            cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    trace, seen = None, 0
    while True:
        if trace is None:
            new = [p for p in (ROOT / "logs").glob("trace-*.jsonl") if p.name not in before]
            trace = new[0] if new else None
        if trace is not None:
            lines = trace.read_text(encoding="utf-8").splitlines()
            for line in lines[seen:]:
                show(json.loads(line))
            seen = len(lines)
        if proc.poll() is not None and trace is not None:
            lines = trace.read_text(encoding="utf-8").splitlines()
            for line in lines[seen:]:
                show(json.loads(line))
            break
        time.sleep(0.05)
    res = json.loads((ROOT / "logs" / "demo_case3.json").read_text(encoding="utf-8"))
    out()
    out(f"DECISION  {res.get('decision')}", "decision")
    for reason in res.get("escalation_reasons") or []:
        out(f"  why: {reason}", "decision")
    summ = res.get("model_summary") or ""
    if summ:
        out("  " + summ[:150] + ("..." if len(summ) > 150 else ""), "dim")
    _cast.close()
    return proc.returncode


def show(e: dict) -> None:
    a, ev, d = e["agent"], e["event"], e.get("data", {})
    if ev == "query":
        out(f"  {a:<16} zetaris  {d['template_id']:<22} rows={d['rows']}  {d['elapsed_ms']} ms", "zetaris")
    elif ev == "audited":
        style = "conflict" if d["status"] == "conflict" else "plain"
        out(f"  {a:<16} {d['finding']} {d['claim']:<20} {d['status']}", style)
    elif ev == "llm_call":
        out(f"  {a:<16} nemotron {e.get('model', '').split('/')[-1]}  {d.get('latency_ms')} ms", "dim")
    elif ev == "plan":
        out(f"  {a:<16} questions: {', '.join(d['questions'])}", "plain")
    elif ev == "severity":
        out(f"  {a:<16} severity {d['score']} ({d['band']})", "plain")
    elif ev == "decision":
        out(f"  {a:<16} {d['decision']}  (provisional: {d.get('provisional')})", "plain")
    elif ev == "start":
        out(f"  {a:<16} start {d['incident_id']} backend={d['backend']}", "plain")


if __name__ == "__main__":
    raise SystemExit(main())
