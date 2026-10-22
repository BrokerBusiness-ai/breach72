"""Deterministic breach severity: ENISA 2013 methodology, SE = DPC x EI + CB.

Reference: ENISA, "Recommendations for a methodology of the assessment of severity
of personal data breaches", December 2013.

  DPC  Data Processing Context, 1..4. Base by data type (simple 1, behavioural 2,
       financial 3, sensitive 4) plus documented context adjustments, clamped to 1..4.
  EI   Ease of Identification: negligible 0.25, limited 0.5, significant 0.75, maximum 1.
       Data rendered unintelligible (state-of-the-art encryption or salted hashing with
       the key/salt not compromised) sets EI to negligible.
  CB   Circumstances of Breach, added:
       confidentiality: none 0, known recipients 0.25, unknown/uncontrolled recipients 0.5
       integrity:       none 0, altered but recoverable 0.25, altered not recoverable 0.5
       availability:    none/short 0, recoverable after delay 0.25, permanent loss 0.5
       malicious intent: +0.5
  Severity bands: SE < 2 low, 2 <= SE < 3 medium, 3 <= SE < 4 high, SE >= 4 very high.

No language model is involved. Every number in the output is traceable to an input field.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict

DATA_TYPE_BASE = {"simple": 1, "behavioural": 2, "financial": 3, "sensitive": 4}
EI_LEVELS = {"negligible": 0.25, "limited": 0.5, "significant": 0.75, "maximum": 1.0}
EI_ORDER = ["negligible", "limited", "significant", "maximum"]
CONF = {"none": 0.0, "known_recipients": 0.25, "unknown_recipients": 0.5}
INTEG = {"none": 0.0, "altered_recoverable": 0.25, "altered_unrecoverable": 0.5}
AVAIL = {"none": 0.0, "recoverable_delay": 0.25, "permanent": 0.5}
BANDS = [(4.0, "very_high"), (3.0, "high"), (2.0, "medium"), (float("-inf"), "low")]
BORDER = 0.25  # distance to a band threshold that triggers human review


@dataclass(frozen=True)
class BreachFacts:
    data_type: str                       # simple | behavioural | financial | sensitive
    dpc_adjustments: tuple[tuple[str, int], ...] = ()   # (reason, +/-1)
    identifiability: str = "maximum"     # EI level before mitigations
    unintelligible: bool = False         # encrypted / strongly hashed, key not compromised
    confidentiality: str = "none"
    integrity: str = "none"
    availability: str = "none"
    malicious: bool = False
    mitigations: tuple[str, ...] = ()    # e.g. "recipient_deletion_confirmed"


@dataclass
class Severity:
    score: float
    band: str
    dpc: int
    ei: float
    cb: float
    components: dict = field(default_factory=dict)
    borderline: bool = False
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def _validate(f: BreachFacts) -> None:
    if f.data_type not in DATA_TYPE_BASE:
        raise ValueError(f"unknown data_type {f.data_type!r}")
    for name, table in (("identifiability", EI_LEVELS), ("confidentiality", CONF), ("integrity", INTEG),
                        ("availability", AVAIL)):
        if getattr(f, name) not in table:
            raise ValueError(f"unknown {name} {getattr(f, name)!r}")
    for reason, delta in f.dpc_adjustments:
        if delta not in (-1, 1):
            raise ValueError(f"DPC adjustment must be +/-1, got {delta} for {reason}")


def assess(f: BreachFacts) -> Severity:
    _validate(f)
    reasons: list[str] = []
    dpc = DATA_TYPE_BASE[f.data_type]
    reasons.append(f"DPC base {dpc} for {f.data_type} data")
    for reason, delta in f.dpc_adjustments:
        dpc += delta
        reasons.append(f"DPC {'+' if delta > 0 else ''}{delta}: {reason}")
    dpc = max(1, min(4, dpc))

    ei_level = f.identifiability
    if f.unintelligible:
        ei_level = "negligible"
        reasons.append("EI negligible: data unintelligible to the recipient (encryption/hashing, key safe)")
    if "recipient_deletion_confirmed" in f.mitigations and ei_level != "negligible":
        ei_level = EI_ORDER[EI_ORDER.index(ei_level) - 1]
        reasons.append(f"EI lowered to {ei_level}: recipient confirmed deletion")
    ei = EI_LEVELS[ei_level]

    conf = f.confidentiality
    if "recipient_deletion_confirmed" in f.mitigations and conf == "unknown_recipients":
        conf = "known_recipients"
        reasons.append("Confidentiality: recipient identified and confirmed deletion")
    cb_parts = {"confidentiality": CONF[conf], "integrity": INTEG[f.integrity],
                "availability": AVAIL[f.availability], "malicious": 0.5 if f.malicious else 0.0}
    cb = sum(cb_parts.values())
    reasons.append(f"EI {ei} ({ei_level} ease of identification)")
    labels = {"confidentiality": conf, "integrity": f.integrity, "availability": f.availability,
              "malicious": "malicious intent"}
    reasons += [f"CB +{v} for {k} ({labels[k]})" for k, v in cb_parts.items() if v]
    score = round(dpc * ei + cb, 4)
    band = next(name for threshold, name in BANDS if score >= threshold)
    borderline = any(abs(score - t) <= BORDER for t in (2.0, 3.0, 4.0))
    if borderline:
        reasons.append(f"Score {score} is within {BORDER} of a band threshold: human review required")
    return Severity(score, band, dpc, ei, cb,
                    {"dpc": dpc, "ei_level": ei_level, "ei": ei, **cb_parts}, borderline, reasons)
