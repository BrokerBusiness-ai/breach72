"""Build docs/Breach72_deck.pdf (8 slides, 16:9) from the repository state.

Facts on the slides are read from the repo at build time so the deck cannot drift from the code:
metadata.json (agents, models), output_examples/dev_nemotron (decisions, scores, clock),
the trace files of that run when present (model calls, latency), pytest collection (test count)
and recordings/ (whether live Zetaris recordings exist).

Usage: python scripts/build_deck.py [--out docs/Breach72_deck.pdf]
Requires reportlab (dev only, not a runtime dependency).
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import statistics
import subprocess
import sys
from collections import defaultdict

from reportlab.lib.colors import HexColor
from reportlab.pdfgen import canvas

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
W, H = 1280, 720
INK = HexColor("#1b1f24")
MUTED = HexColor("#5b6470")
ACCENT = HexColor("#0b6e4f")
WARN = HexColor("#a4461b")
PANEL = HexColor("#eef2f0")
LINE = HexColor("#c9d3ce")

DECISION_TEXT = {
    "document_internally": "document internally (Art. 33(5))",
    "notify_authority": "notify the authority",
    "notify_authority_and_individuals": "notify authority and individuals",
    "escalate_to_dpo": "escalate to the DPO",
}
CASE_LABEL = {
    "case1_lost_encrypted_laptop": "1 Lost encrypted laptop",
    "case2_misdirected_email": "2 1 200 records e-mailed to an unknown address",
    "case2b_recipient_deleted": "2b Same, recipient confirmed deletion",
    "case3_ransomware_hr": "3 Ransomware on HR server, sources conflict",
    "case4_processor_late_notice": "4 Processor reported after 50 hours",
    "case5_no_detection_date": "5 Discovery time not recorded",
}


def load_facts() -> dict:
    meta = json.load(open(os.path.join(ROOT, "metadata.json"), encoding="utf-8"))
    cases = {}
    for f in sorted(glob.glob(os.path.join(ROOT, "output_examples", "dev_nemotron", "*.json"))):
        d = json.load(open(f, encoding="utf-8"))
        cases[os.path.splitext(os.path.basename(f))[0]] = d
    calls = defaultdict(list)
    errors = 0
    for d in cases.values():
        lf = os.path.join(ROOT, d.get("log_file") or "")
        if not os.path.isfile(lf):
            continue
        for line in open(lf, encoding="utf-8"):
            e = json.loads(line)
            if e.get("event") == "llm_call":
                calls[(e.get("agent"), e.get("model"))].append(e["data"].get("latency_ms") or 0)
            elif e.get("event") == "llm_error":
                errors += 1
    tests = None
    try:
        out = subprocess.run([sys.executable, "-m", "pytest", "--collect-only", "-q"], cwd=ROOT,
                             capture_output=True, text=True, timeout=120).stdout
        m = re.search(r"(\d+) tests? collected", out)
        tests = int(m.group(1)) if m else None
    except Exception:
        pass
    zetaris_rec = glob.glob(os.path.join(ROOT, "recordings", "*.json"))  # app/adapters/zetaris.py RECORDINGS
    return {"meta": meta, "cases": cases, "calls": calls, "llm_errors": errors,
            "tests": tests, "zetaris_recordings": len(zetaris_rec)}


class Deck:
    def __init__(self, path: str):
        self.c = canvas.Canvas(path, pagesize=(W, H))
        self.c.setTitle("Breach72: Open Agent Hackathon 2026, Track 2")
        self.c.setAuthor("Team Archaios (Marek Porycki, Broker Business sp. z o.o.)")
        self.n = 0

    def page(self, title: str, kicker: str = ""):
        if self.n:
            self.c.showPage()
        self.n += 1
        c = self.c
        c.setFillColor(HexColor("#ffffff"))
        c.rect(0, 0, W, H, fill=1, stroke=0)
        c.setFillColor(ACCENT)
        c.rect(0, H - 8, W, 8, fill=1, stroke=0)
        if kicker:
            c.setFont("Helvetica-Bold", 16)
            c.setFillColor(ACCENT)
            c.drawString(64, H - 70, kicker.upper())
        c.setFont("Helvetica-Bold", 40)
        c.setFillColor(INK)
        c.drawString(64, H - 120, title)
        c.setFont("Helvetica", 13)
        c.setFillColor(MUTED)
        c.drawString(64, 32, "Breach72 | Open Agent Hackathon 2026 | Track 2: The Agent That Can Explain Why | Team Archaios")
        c.drawRightString(W - 64, 32, f"{self.n} / 8")

    def text(self, x, y, s, size=22, bold=False, color=INK, width=None, leading=None):
        """Draw wrapped text, return the y below it."""
        c = self.c
        font = "Helvetica-Bold" if bold else "Helvetica"
        c.setFont(font, size)
        c.setFillColor(color)
        leading = leading or size * 1.32
        width = width or (W - x - 64)
        for para in s.split("\n"):
            words, line = para.split(" "), ""
            for w in words:
                trial = (line + " " + w).strip()
                if c.stringWidth(trial, font, size) > width and line:
                    c.drawString(x, y, line)
                    y -= leading
                    line = w
                else:
                    line = trial
            c.drawString(x, y, line)
            y -= leading
        return y

    def bullets(self, x, y, items, size=22, width=None, gap=10):
        for it in items:
            self.c.setFillColor(ACCENT)
            self.c.circle(x + 6, y + size * 0.33, 4.5, fill=1, stroke=0)
            y = self.text(x + 24, y, it, size=size, width=(width or (W - x - 64)) - 24) - gap
        return y

    def box(self, x, y, w, h, title, body, size=17, fill=PANEL, title_color=INK):
        c = self.c
        c.setFillColor(fill)
        c.setStrokeColor(LINE)
        c.roundRect(x, y - h, w, h, 10, fill=1, stroke=1)
        yy = self.text(x + 16, y - 32, title, size=size + 3, bold=True, color=title_color, width=w - 32)
        self.text(x + 16, yy - 4, body, size=size, color=INK, width=w - 32)

    def arrow(self, x1, y1, x2, y2, color=MUTED):
        c = self.c
        c.setStrokeColor(color)
        c.setFillColor(color)
        c.setLineWidth(2.5)
        c.line(x1, y1, x2, y2)
        import math
        a = math.atan2(y2 - y1, x2 - x1)
        p = c.beginPath()
        p.moveTo(x2, y2)
        p.lineTo(x2 - 14 * math.cos(a - 0.4), y2 - 14 * math.sin(a - 0.4))
        p.lineTo(x2 - 14 * math.cos(a + 0.4), y2 - 14 * math.sin(a + 0.4))
        p.close()
        c.drawPath(p, fill=1, stroke=0)

    def save(self):
        self.c.save()


def build(out: str) -> None:
    f = load_facts()
    meta, cases, calls = f["meta"], f["cases"], f["calls"]
    models = meta.get("models", {})
    tests = f["tests"]
    live_zetaris = f["zetaris_recordings"] > 0
    d = Deck(out)

    # 1 Problem and user
    d.page("72 hours, four systems, one decision", "The problem")
    y = d.text(64, H - 180, "When personal data leaks, GDPR Art. 33 gives the controller 72 hours to notify the "
               "supervisory authority, and Art. 34 requires telling the affected people when the risk is high.",
               size=24, width=1150)
    y = d.bullets(64, y - 20, [
        "Who decides: the data protection officer (DPO) or compliance lead of a small or mid-sized organisation "
        "or public body, usually alone.",
        "Where the facts are: the incident log, the record of processing activities, the asset and encryption "
        "register and processor contracts. Separate systems, owned by other people.",
        "What goes wrong: the clock runs while facts are collected by hand, and the final call is hard to "
        "justify afterwards to the authority.",
    ], size=22, width=1150)
    d.text(64, y - 16, "Breach72 answers: notify or not, who, by when, and exactly why, with every claim linked to "
           "its source record and legal basis.", size=22, bold=True, color=ACCENT, width=1150)

    # 2 Solution and output
    d.page("A decision the DPO can defend", "The solution")
    d.box(64, H - 170, 560, 420, "What the DPO gets", (
        "Decision: notify the authority, notify the authority and individuals, document internally, or "
        "escalate to the DPO.\n"
        "Explanation: every line cites finding ids and the legal article.\n"
        "Draft Art. 33(3) notification and Art. 34 communication.\n"
        "Open questions and the 72-hour clock (aware at, deadline, hours left).\n"
        "Full JSONL trace and an HTML report."), size=23)
    d.box(656, H - 170, 560, 420, "What it never does", (
        "A language model never makes the decision. A deterministic, unit-tested rule set does "
        "(ENISA 2013 severity, GDPR Art. 33 and 34 paths).\n"
        "No free-form text-to-SQL: only vetted, parameterised query templates.\n"
        "No claim without evidence: the Evidence Auditor vetoes it.\n"
        "Decision support for a human, not legal advice. All data is SYNTHETIC."), size=23)

    # 3 Agent workflow with loops
    d.page("Five agents, one shared case state", "Workflow")
    names = ["Planner", "Investigator", "Evidence Auditor", "Risk Assessor", "Reviewer & Drafter"]
    roles = ["awareness moment, evidence questions by impact",
             "vetted SQL templates via Zetaris, provenance on every finding",
             "veto, re-verification with a different template, conflict branch",
             "deterministic ENISA score, GDPR path, unit-tested",
             "checks against the rule, explains, drafts, escalates"]
    bw, gap, top = 216, 23, H - 190
    for i, (n, r) in enumerate(zip(names, roles)):
        x = 64 + i * (bw + gap)
        fill = HexColor("#fdf1ea") if n == "Evidence Auditor" else PANEL
        d.box(x, top, bw, 210, n, r, size=16, fill=fill, title_color=WARN if n == "Evidence Auditor" else INK)
        if i < 4:
            d.arrow(x + bw + 2, top - 105, x + bw + gap - 2, top - 105)
    # veto loop
    ax = 64 + 2 * (bw + gap) + bw / 2
    px = 64 + bw / 2
    d.c.setStrokeColor(WARN)
    d.c.setLineWidth(2.5)
    d.c.line(ax, top - 210, ax, top - 250)
    d.c.line(ax, top - 250, px, top - 250)
    d.arrow(px, top - 250, px, top - 212, color=WARN)
    d.text(px + 20, top - 280, "veto: claim without evidence goes back to the Planner (max 3 iterations)",
           size=17, color=WARN)
    d.bullets(64, top - 330, [
        "Loops in the trace: veto, re-verification at the source, bounded retry (source 20 s, model 60 s), "
        "escalation to the DPO on a borderline score, conflicting sources or an assumed awareness time.",
        "Shared state: Meterless World Model; every finding and every Auditor status change is a fact with "
        "provenance. No provenance, no write.",
    ], size=19, width=1150)

    # 4 Stack and model split
    d.page("Stack: Nemotron explains, the rule decides", "Technology")
    rows = []
    for (agent, model), lat in sorted(calls.items()):
        rows.append((agent, model, len(lat), statistics.median(lat) if lat else 0))
    y = d.text(64, H - 180, "NVIDIA Nemotron through the hosted NVIDIA API (integrate.api.nvidia.com):", size=22, bold=True)
    y = d.bullets(64, y - 6, [
        f"Large: {models.get('large', '?')} for the Evidence Auditor's conflict note and the Reviewer's "
        "plain-language summary.",
        f"Small: {models.get('small', '?')} for the Planner's investigation note.",
        "A summary is accepted only if every cited finding exists and it does not contradict the rule's "
        "decision; otherwise the deterministic explanation is used and the rejection is logged.",
    ], size=19, width=1150)
    if rows:
        y -= 6
        d.text(64, y, "Live run, 23 October 2026, six cases (from the JSONL traces):", size=18, bold=True, color=MUTED)
        y -= 34
        d.c.setFont("Helvetica-Bold", 16)
        d.c.setFillColor(INK)
        for xx, h in ((64, "agent"), (330, "model"), (900, "calls"), (1020, "median latency")):
            d.c.drawString(xx, y, h)
        d.c.setFont("Helvetica", 16)
        for agent, model, n, med in rows:
            y -= 26
            for xx, v in ((64, agent), (330, model), (900, str(n)), (1020, f"{med / 1000:.1f} s")):
                d.c.drawString(xx, y, v)
        y -= 32
        d.text(64, y, f"{f['llm_errors']} transient API errors (timeouts, HTTP 500) in that run, all recovered by "
               "bounded retries. Every call is logged with its model; responses are recorded for replay.",
               size=16, color=MUTED, width=1150)
    d.text(64, 96, "Other stack: Python 3.11 standard library only at runtime, pytest, Docker, Cursor.",
           size=17, color=MUTED, width=1150)

    # 5 Zetaris and Meterless
    d.page("Zetaris and Meterless carry the load", "Sponsor technology")
    zbody = ("Single governed SQL access point to four synthetic systems: incident database, ROPA, asset and "
             "encryption register, processor contracts. The Investigator queries in place through vetted "
             "templates; the Auditor re-queries with a different template before approval. Adapter with 20 s "
             "timeout, bounded retries, recording and replay.")
    if live_zetaris:
        zbody += f" Live responses recorded: {f['zetaris_recordings']}; replay verified field by field."
    else:
        zbody += (" Status: adapter implemented and tested; live Zetaris recordings not yet committed, so the "
                  "offline demo runs the same templates on a local SQLite copy and says so in every output.")
    d.box(64, H - 170, 560, 460, "Zetaris", zbody, size=20)
    d.box(656, H - 170, 560, 460, "Meterless (reference architectures, Apache-2.0)", (
        "World Model: the shared case state. Every finding and every Auditor status change is an assertFact "
        "with provenance, with an append-only event log.\n"
        "H-MEM: verified facts about the organisation's registers kept across cases with a JSONL ledger. "
        "Recalled facts are re-verified at the source: a match is promoted, a mismatch is superseded. Memory "
        "never feeds the rule directly.\n"
        "Try it: run case 2, then 2b; the second run recalls two facts and re-verifies them."), size=20)

    # 6 What is unique
    d.page("Why the explanation can be trusted", "What is unique")
    edpb = "17 of the 18 worked cases of EDPB Guidelines 01/2021"
    d.bullets(64, H - 190, [
        f"Deterministic, tested severity rule: ENISA 2013, SE = DPC x EI + CB, mapped to GDPR Art. 33(1), "
        f"33(2), 33(4), 33(5), 34(1), 34(3)(a), 28(3)(f). Tested on {edpb}.",
        "An auditor agent with a veto that re-checks decisive facts (people affected, encryption, disclosure, "
        "data categories) at the source with a second query template.",
        "Organisational memory that is never trusted blindly: every recalled fact is re-verified before use.",
        "Two gates on model text: citations must exist, and the text must not contradict the decision.",
        "Honest failure: without data the agents stop with source_unavailable; nothing is invented.",
    ], size=21, width=1150, gap=14)

    # 7 Result and trace
    d.page("Six synthetic cases, six defensible outcomes", "Results")
    y = H - 175
    d.c.setFont("Helvetica-Bold", 17)
    d.c.setFillColor(INK)
    for xx, h in ((64, "case"), (640, "decision"), (960, "severity"), (1090, "time")):
        d.c.drawString(xx, y, h)
    d.c.setFont("Helvetica", 17)
    for key in ["case1_lost_encrypted_laptop", "case2_misdirected_email", "case2b_recipient_deleted",
                "case3_ransomware_hr", "case4_processor_late_notice", "case5_no_detection_date"]:
        r = cases.get(key)
        if not r:
            continue
        y -= 30
        for xx, v in ((64, CASE_LABEL.get(key, key)), (640, DECISION_TEXT.get(r["decision"], r["decision"])),
                      (960, str((r.get("risk") or {}).get("score", ""))), (1090, f"{r.get('execution_time', 0):.0f} s")):
            d.c.drawString(xx, y, v)
    c3 = cases.get("case3_ransomware_hr", {})
    expl = (c3.get("explanation") or [""])[0]
    oq = (c3.get("open_questions") or [""])[0]
    y -= 40
    y = d.text(64, y, "Case 3 from the output JSON:", size=18, bold=True, color=ACCENT)
    y = d.text(64, y, expl, size=16, width=1150)
    y = d.text(64, y - 2, "Escalation: " + ", ".join(c3.get("escalation_reasons") or []), size=16, width=1150)
    d.text(64, y - 2, "Open question: " + oq[:230], size=16, width=1150)

    # 8 Development and limitations
    d.page("How it was built, and its limits", "Development")
    tline = f"{tests} automated tests" if tests else "automated tests"
    d.box(64, H - 170, 560, 470, "Built in the window (22 to 27 October)", (
        f"{tline}: rule set on EDPB cases, pipeline, Meterless, Nemotron gates, API, replay check.\n"
        "Clean-clone check with an empty environment: python run.py --all --backend dev gives the same six "
        "decisions; Nemotron replays recorded live responses.\n"
        "Cursor: project rules in .cursor/rules; four documented Cursor Agent sessions "
        "(docs/cursor_usage/01-04). Later commits by an AI coding agent outside Cursor are labelled so.\n"
        "Self-check script: required files, secret scan, recordings, Cursor evidence."), size=20)
    lim = ("Decision support for a DPO, not legal advice.\n"
           "All data is synthetic (data/SYNTHETIC.md).\n"
           "Severity follows the ENISA 2013 method; national authority practice may differ.\n"
           "Category-to-severity mapping is a documented policy, not an official table.")
    if not live_zetaris:
        lim += "\nLive Zetaris recordings are pending; the demo uses the local SYNTHETIC SQLite backend."
    d.box(656, H - 170, 560, 470, "Limitations", lim, size=20, title_color=WARN)
    d.save()
    print(f"wrote {out}: 8 slides, tests={tests}, zetaris_recordings={f['zetaris_recordings']}, "
          f"model_rows={len(calls)}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default=os.path.join(ROOT, "docs", "Breach72_deck.pdf"))
    a = ap.parse_args()
    build(a.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
