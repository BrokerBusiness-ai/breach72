"""Render a Breach72 result JSON as a single self-contained HTML report.

    python report.py output_examples/case2_misdirected_email.json            # writes .html next to it
    python report.py result.json --out report.html
"""
from __future__ import annotations

import argparse
import html
import json
from pathlib import Path

CSS = """
body{font:15px/1.5 system-ui,Segoe UI,Arial,sans-serif;margin:0;background:#f6f7f9;color:#1d2433}
main{max-width:980px;margin:0 auto;padding:24px 16px}
h1{font-size:22px;margin:0 0 4px}h2{font-size:17px;margin:28px 0 8px}
.card{background:#fff;border:1px solid #dde2ea;border-radius:8px;padding:16px;margin:12px 0}
.decision{font-size:20px;font-weight:700}.esc{color:#a4430f}.ok{color:#1d6b3a}
.pill{display:inline-block;border-radius:12px;padding:2px 10px;font-size:13px;background:#eef1f6;margin:2px 4px 2px 0}
table{border-collapse:collapse;width:100%;font-size:14px}td,th{border-bottom:1px solid #e6e9ef;padding:6px;text-align:left;vertical-align:top}
.verified{color:#1d6b3a}.conflict{color:#a4430f;font-weight:600}.vetoed{color:#888;text-decoration:line-through}
code{background:#eef1f6;padding:1px 4px;border-radius:4px;font-size:13px}.muted{color:#5b6576;font-size:13px}
"""


def esc(v) -> str:
    return html.escape(str(v))


def render(r: dict) -> str:
    if r.get("status") != "ok":
        e = r.get("error", {})
        return f"<!doctype html><meta charset=utf-8><title>Breach72 error</title><style>{CSS}</style><main>" \
               f"<h1>Breach72: run failed</h1><div class=card>{esc(e.get('type'))}: {esc(e.get('message'))}</div></main>"
    clock = r.get("clock", {})
    esc_cls = "esc" if r["decision"] == "escalate_to_dpo" else "ok"
    parts = [f"<!doctype html><html lang=en><meta charset=utf-8><meta name=viewport content='width=device-width'>"
             f"<title>Breach72 {esc(r['incident_id'])}</title><style>{CSS}</style><main>",
             f"<h1>Breach72: {esc(r['incident_id'])}</h1><div class=muted>Run {esc(r['run_id'])}, assessed as of "
             f"{esc(r['as_of'])}. Data backends: {esc(', '.join(r.get('data_backends', [])))}. SYNTHETIC data.</div>",
             f"<div class=card><div class='decision {esc_cls}'>{esc(r['decision_text'])}</div>"
             f"<div>Provisional rule outcome: <code>{esc(r['provisional_decision'])}</code>. "
             f"Severity {esc(r['risk']['score'])} ({esc(r['risk']['band'])}, {esc(r['risk']['method'])}).</div>"]
    for reason in r.get("escalation_reasons", []):
        parts.append(f"<div class=esc>Escalation: {esc(reason)}</div>")
    parts.append(f"<div>72-hour clock: aware at {esc(clock.get('aware_at'))}"
                 f"{' (assumed)' if clock.get('assumed') else ''}, deadline {esc(clock.get('deadline'))}, "
                 f"hours left {esc(clock.get('hours_left'))}.</div>")
    parts.append("".join(f"<span class=pill>{esc(f)}</span>" for f in r.get("flags", [])) + "</div>")
    if r.get("model_summary"):
        parts.append(f"<h2>Summary (NVIDIA Nemotron, validated against finding ids)</h2><div class=card>"
                     f"{esc(r['model_summary'])}</div>")
    parts.append("<h2>Why</h2><div class=card><ol>" + "".join(f"<li>{esc(x)}</li>" for x in r["explanation"])
                 + "</ol></div>")
    parts.append("<h2>Findings and evidence</h2><div class=card><table><tr><th>Id</th><th>Claim</th><th>Status</th>"
                 "<th>Evidence</th></tr>")
    for f in r["findings"]:
        ev = "<br>".join(f"<code>{esc(e['template_id'])}</code> {esc(e['backend'])} {esc(', '.join(e['tables']))} "
                         f"rows {esc(e['row_ref'])}" for e in f["evidence"])
        note = f"<div class=muted>{esc(f['note'])}</div>" if f.get("note") else ""
        parts.append(f"<tr><td>{esc(f['id'])}</td><td>{esc(f['statement'])}{note}</td>"
                     f"<td class={esc(f['status'])}>{esc(f['status'])}<div class=muted>{esc(f['confidence'])}"
                     f"{' via ' + esc(f['verified_by']) if f.get('verified_by') else ''}</div></td><td>{ev}</td></tr>")
    parts.append("</table></div>")
    d = r["draft_notification"]
    parts.append("<h2>Draft notification to the supervisory authority (Art. 33(3))</h2><div class=card><table>")
    parts += [f"<tr><th>{esc(k)}</th><td>{esc(v)}</td></tr>" for k, v in d["to_supervisory_authority"].items()]
    parts.append("</table>")
    for k in ("phased", "reasons_for_delay"):
        if d.get(k):
            parts.append(f"<p>{esc(d[k])}</p>")
    parts.append("</div>")
    if r.get("open_questions"):
        parts.append("<h2>Open questions for the DPO</h2><div class=card><ul>"
                     + "".join(f"<li>{esc(q)}</li>" for q in r["open_questions"]) + "</ul></div>")
    parts.append("<h2>Limitations</h2><div class=card><ul>"
                 + "".join(f"<li>{esc(q)}</li>" for q in r["limitations"]) + "</ul>"
                 f"<div class=muted>Trace: {esc(r['log_file'])}. Agents: {esc(', '.join(r['agents']))}. "
                 f"Iterations: {esc(r['iterations'])}. Execution time: {esc(r['execution_time'])} s.</div></div>")
    parts.append("</main></html>")
    return "\n".join(parts)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Render a Breach72 result JSON as HTML")
    ap.add_argument("result", type=Path)
    ap.add_argument("--out", type=Path, default=None)
    a = ap.parse_args(argv)
    r = json.loads(a.result.read_text(encoding="utf-8"))
    out = a.out or a.result.with_suffix(".html")
    out.write_text(render(r), encoding="utf-8")
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
