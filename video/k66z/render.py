"""Render the Zetaris Demo Challenge video from the real live-run cast.

Input:  logs/demo_cast.jsonl (written by scripts/demo_live.py during a live run on Zetaris)
Output: video/k66z/out/<NAME>.mp4  (1920x1080, 30 fps, H.264 + AAC)

Narration: synthetic voice (edge-tts), disclosed on the end card.
"""
from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
OUT = HERE / "out"
FR = HERE / "frames"
W, H, FPS = 1920, 1080, 30
NAME = sys.argv[1] if len(sys.argv) > 1 else "ARCHAIOS_271026"
VOICE = "en-US-AndrewNeural"

F = "C:/Windows/Fonts/"
MONO = ImageFont.truetype(F + "consola.ttf", 23)
MONO_B = ImageFont.truetype(F + "consolab.ttf", 23)
SANS = ImageFont.truetype(F + "segoeui.ttf", 40)
SANS_S = ImageFont.truetype(F + "segoeui.ttf", 30)
SANS_XS = ImageFont.truetype(F + "segoeui.ttf", 24)
SANS_B = ImageFont.truetype(F + "segoeuib.ttf", 64)
SANS_BM = ImageFont.truetype(F + "segoeuib.ttf", 44)

BG = (13, 17, 23)
PANEL = (22, 27, 34)
FG = (230, 237, 243)
DIM = (139, 148, 158)
ACC = (88, 166, 255)
GREEN = (63, 185, 80)
RED = (248, 81, 73)
YEL = (227, 179, 65)
STYLE = {"cmd": (ACC, MONO_B), "sql": ((210, 168, 255), MONO), "head": (FG, MONO_B), "row": (FG, MONO),
         "dim": (DIM, MONO), "zetaris": (GREEN, MONO), "conflict": (RED, MONO_B), "decision": (YEL, MONO_B),
         "plain": (FG, MONO)}

NARR = {
    "title": "Breach72, running live on Zetaris.",
    "problem": ("When a personal data breach hits, a data protection officer has seventy-two hours to decide "
                "whether to notify the regulator. The facts are spread over four systems: the incident log, the "
                "record of processing activities, the asset and encryption register, and processor contracts."),
    "federate": ("Each system is its own database, registered in Zetaris as its own datasource. One SQL statement "
                 "asks all four at once: this HR server held national ID numbers and sick-leave certificates, and "
                 "it was not encrypted."),
    "agents": ("Now the agents run the ransomware case, live. Every green line is a governed query through Zetaris. "
               "The investigator gathers the evidence, and the evidence auditor re-checks each decisive fact at the "
               "source, with a different query."),
    "conflict": ("And here it catches something. The IT report says no data left the server. But the access log, "
                 "read through Zetaris, shows two gigabytes sent to an external address. The sources disagree on a "
                 "decisive fact, so Breach72 does not guess. It escalates to the data protection officer, with both "
                 "records cited."),
    "why": ("Why I think Zetaris is amazing. I'm Marek, a psychologist and court mediator from Poland, and the hard "
            "part of a breach decision is not the reasoning. It is the evidence. The moment it clicked was when my "
            "evidence auditor rejected a claim and simply asked Zetaris again, through a different query, against "
            "the original source, without me copying a single table. That is what surprised me: one governed SQL "
            "access point turned four silos into one question. It let me build an agent that can prove every "
            "sentence it writes, which is exactly what a data protection officer needs before signing a "
            "notification to the regulator. And because every answer comes back with its source, the same run can be "
            "replayed, line by line, for anyone who wants to check it."),
    "end": "Breach72: every sentence backed by a source, through Zetaris.",
}


async def tts_all() -> dict[str, float]:
    import edge_tts
    durs = {}
    for k, text in NARR.items():
        mp3 = OUT / f"vo_{k}.mp3"
        if not mp3.exists():
            await edge_tts.Communicate(text, VOICE, rate="-4%").save(str(mp3))
        d = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                            str(mp3)], capture_output=True, text=True).stdout.strip()
        durs[k] = float(d)
    return durs


def wrap(draw, text, font, width):
    words, lines, cur = text.split(), [], ""
    for w in words:
        t = (cur + " " + w).strip()
        if draw.textlength(t, font=font) <= width:
            cur = t
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def base(label: str | None = None) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    im = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(im)
    d.rectangle([0, 0, W, 6], fill=ACC)
    d.text((60, H - 52), "Breach72  |  team ARCHAIOS  |  Open Agent Hackathon 2026  |  synthetic data only",
           font=SANS_XS, fill=DIM)
    if label:
        tw = d.textlength(label, font=SANS_XS)
        d.text((W - 60 - tw, H - 52), label, font=SANS_XS, fill=DIM)
    return im, d


def card(title: str, body: list[str], sub: str | None = None) -> Image.Image:
    im, d = base()
    d.text((140, 260), title, font=SANS_B, fill=FG)
    y = 380
    for para in body:
        for line in wrap(d, para, SANS, 1600):
            d.text((140, y), line, font=SANS, fill=FG if not para.startswith("-") else DIM)
            y += 58
        y += 18
    if sub:
        d.text((140, y + 20), sub, font=SANS_S, fill=ACC)
    return im


def terminal(lines: list[dict], highlight: str | None = None, panel: list[tuple[str, tuple]] | None = None,
             caption: str = "") -> Image.Image:
    im, d = base("live run, 27 Oct 2026, local Zetaris platform, slowed for reading")
    tx0, ty0, tx1 = 50, 40, (W - 50 if not panel else 1180)
    d.rounded_rectangle([tx0, ty0, tx1, H - 80], 14, fill=PANEL)
    for i, c in enumerate([(255, 95, 86), (255, 189, 46), (39, 201, 63)]):
        d.ellipse([tx0 + 20 + i * 26, ty0 + 16, tx0 + 36 + i * 26, ty0 + 32], fill=c)
    d.text((tx0 + 110, ty0 + 10), "breach72 @ zetaris (localhost:3000)", font=SANS_XS, fill=DIM)
    lh, top = 30, ty0 + 52
    maxl = (H - 100 - top) // lh
    shown = lines[-maxl:]
    for i, ln in enumerate(shown):
        col, font = STYLE.get(ln["style"], STYLE["plain"])
        text = ln["text"]
        maxw = tx1 - tx0 - 40
        while text and d.textlength(text, font=font) > maxw:
            text = text[:-1]
        y = top + i * lh
        if highlight and highlight in ln["text"]:
            d.rectangle([tx0 + 8, y - 3, tx1 - 8, y + lh - 4], outline=RED, width=3)
        d.text((tx0 + 20, y), text, font=font, fill=col)
    if panel:
        px = 1210
        d.rounded_rectangle([px, ty0, W - 50, H - 80], 14, fill=(30, 22, 26))
        y = ty0 + 30
        for text, (col, font) in panel:
            for line in wrap(d, text, font, W - 50 - px - 60):
                d.text((px + 30, y), line, font=font, fill=col)
                y += 40 if font in (SANS_S, SANS_XS) else 52
            y += 14
    if caption:
        d.text((tx0 + 20, H - 120), caption, font=SANS_XS, fill=YEL)
    return im


def write(im: Image.Image, n: int, seq: list) -> None:
    p = FR / f"f{len(seq):04d}.png"
    im.save(p)
    seq.append((p, n))


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    FR.mkdir(exist_ok=True)
    for p in FR.glob("*.png"):
        p.unlink()
    durs = asyncio.run(tts_all())
    cast = [json.loads(l) for l in (ROOT / "logs" / "demo_cast.jsonl").read_text(encoding="utf-8").splitlines()]
    run_i = next(i for i, c in enumerate(cast) if c["text"].startswith("$ python run.py"))
    part_a, part_b = cast[:run_i], cast[run_i:]
    seq: list = []  # (png, frames)
    audio: list = []  # (key, start_seconds)
    t = 0.0

    def scene(key, frames_fn, pad=1.0):
        nonlocal t
        dur = durs[key] + pad
        audio.append((key, t + 0.4))
        frames_fn(dur)
        t += dur

    scene("title", lambda dur: write(card("Breach72 on Zetaris", [
        "Agents that decide, within GDPR's 72 hours, whether a breach must be notified, and prove why."],
        "Zetaris Demo Challenge  |  team ARCHAIOS"), int(dur * FPS), seq), pad=1.6)

    scene("problem", lambda dur: write(card("72 hours, four systems", [
        "Incident log  |  Record of processing activities  |  Asset and encryption register  |  Processor contracts",
        "GDPR Art. 33: notify the supervisory authority within 72 hours. Art. 34: tell the people affected.",
        "- Every fact the decision needs lives in a different system."]), int(dur * FPS), seq), pad=0.8)

    def reveal(lines_src, dur, prefix, highlight=None, caption=""):
        n = len(lines_src)
        total = int(dur * FPS)
        span = max(1.0, lines_src[-1]["t"] - lines_src[0]["t"])
        reveal_frames = int(total * 0.8)
        times = [int((c["t"] - lines_src[0]["t"]) / span * reveal_frames) for c in lines_src]
        times = [max(times[i], i * 2) for i in range(n)]  # at least 2 frames apart
        cur = 0
        for k in range(n):
            nxt = times[k + 1] if k + 1 < n else total
            frames = max(1, nxt - max(cur, times[k]))
            write(terminal(prefix + lines_src[:k + 1], highlight, caption=caption), frames, seq)
            cur = max(cur, times[k]) + frames

    scene("federate", lambda dur: reveal(part_a, dur, []), pad=1.2)
    scene("agents", lambda dur: reveal(part_b, dur, part_a[-8:]), pad=1.0)

    upd = "UPD-004  it_report  IT_REPORT_NO_EXFIL: Initial IT report: no evidence of data exfiltration."
    log = "LOG-0004  outbound_transfer  2 147 483 648 bytes  to 198.51.100.23  (03:12)"
    panel = [("Sources disagree on a decisive fact", (RED, SANS_BM)),
             ("incident_db.incident_updates", (DIM, SANS_XS)), (upd, (FG, SANS_S)),
             ("incident_db.access_logs (re-queried through Zetaris)", (DIM, SANS_XS)), (log, (FG, SANS_S)),
             ("Auditor: F03 disclosure = conflict. Decision: escalate to the DPO, provisional: notify the "
              "authority and the people affected, both records cited.", (YEL, SANS_S))]
    scene("conflict", lambda dur: write(terminal(part_a[-6:] + part_b, "F03 disclosure", panel), int(dur * FPS), seq),
          pad=1.2)

    scene("why", lambda dur: write(card("Why Zetaris was amazing", [
        "My auditor agent rejected a claim and simply asked Zetaris again, with a different query, against the "
        "original source. No table copied.",
        "One governed SQL access point turned four silos into one question.",
        "So the agent can prove every sentence it writes: exactly what a DPO needs before signing a "
        "notification."], "Marek Porycki, team ARCHAIOS"), int(dur * FPS), seq), pad=1.2)

    scene("end", lambda dur: write(card("Breach72", [
        "Every sentence backed by a source, through Zetaris.",
        "github.com/BrokerBusiness-ai/breach72",
        "- Synthetic data only. Decision support, not legal advice. Narration: synthetic voice reading the "
        "author's own statement."]), int(dur * FPS), seq), pad=2.0)

    # concat list for frames
    lst = OUT / "frames.txt"
    with open(lst, "w", encoding="utf-8") as fh:
        for p, n in seq:
            fh.write(f"file '{p.as_posix()}'\nduration {n / FPS:.4f}\n")
        fh.write(f"file '{seq[-1][0].as_posix()}'\n")
    total = sum(n for _, n in seq) / FPS
    # audio mix with delays
    inputs, filt = [], []
    for i, (k, start) in enumerate(audio):
        inputs += ["-i", str(OUT / f"vo_{k}.mp3")]
        filt.append(f"[{i + 1}:a]adelay={int(start * 1000)}|{int(start * 1000)}[a{i}]")
    filt.append("".join(f"[a{i}]" for i in range(len(audio))) + f"amix=inputs={len(audio)}:normalize=0[aout]")
    filt.append(f"[0:v]fps={FPS},format=yuv420p[vout]")
    mp4 = OUT / f"{NAME}.mp4"
    cmd = ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(lst), *inputs,
           "-filter_complex", ";".join(filt), "-map", "[vout]", "-map", "[aout]",
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
           "-c:a", "aac", "-b:a", "160k", "-t", f"{total:.2f}", "-movflags", "+faststart", str(mp4)]
    subprocess.run(cmd, check=True, capture_output=True)
    print(json.dumps({"file": str(mp4), "seconds": round(total, 1), "frames_png": len(seq),
                      "segments": {k: round(v, 1) for k, v in durs.items()}}))


if __name__ == "__main__":
    main()
