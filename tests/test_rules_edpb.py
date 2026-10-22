"""Rule set tested against the worked examples in EDPB Guidelines 01/2021
on Examples regarding Personal Data Breach Notification (version 2.0, adopted 14 December 2021).

For each case the facts are encoded by the author from the case narrative; the
expected outcome (notify SA, communicate to data subjects) is the EDPB conclusion.
"""
from datetime import datetime

import pytest

from app.rules.enisa_severity import BreachFacts, assess
from app.rules.gdpr_paths import CaseContext, decide

AWARE = datetime.fromisoformat("2026-10-20T09:00:00+02:00")

EDPB_CASES = [
    # id, title, facts, unintelligible_for_34_3_a, expected (notify_sa, notify_ds)
    ("01", "Ransomware with proper backup and without exfiltration",
     BreachFacts("behavioural", (), "limited", unintelligible=True,
                 availability="none", malicious=True), True, (False, False)),
    ("02", "Ransomware without proper backup",
     BreachFacts("behavioural", (), "maximum", availability="recoverable_delay", malicious=True), False,
     (True, False)),
    ("03", "Ransomware with backup and without exfiltration in a hospital",
     BreachFacts("sensitive", (), "maximum", availability="recoverable_delay", malicious=True), False, (True, True)),
    ("04", "Ransomware without backup and with exfiltration",
     BreachFacts("financial", (), "maximum", confidentiality="unknown_recipients", availability="permanent",
                 malicious=True), False, (True, True)),
    ("05", "Exfiltration of job application data from a website",
     BreachFacts("behavioural", (), "maximum", confidentiality="unknown_recipients", integrity="altered_recoverable",
                 malicious=True), False, (True, True)),
    ("06", "Exfiltration of hashed password from a website",
     BreachFacts("simple", (), "limited", unintelligible=True, confidentiality="unknown_recipients", malicious=True),
     True, (False, False)),
    ("09", "Accidental transmission of data to a trusted third party",
     BreachFacts("behavioural", (), "maximum", confidentiality="known_recipients",
                 mitigations=("recipient_deletion_confirmed",)), False, (False, False)),
    ("10", "Stolen material storing encrypted personal data",
     BreachFacts("behavioural", (("data of children", 1),), "maximum", unintelligible=True, malicious=True), True,
     (False, False)),
    ("11", "Stolen material storing non-encrypted personal data",
     BreachFacts("simple", (("identity data enabling identity fraud", 1), ("very large number of subjects", 1)),
                 "maximum", confidentiality="unknown_recipients", malicious=True), False, (True, True)),
    ("07", "Credential stuffing attack on a banking website",
     # about 100 000 customers' identifying data exposed, about 2 000 accounts logged into
     BreachFacts("financial", (), "maximum", confidentiality="unknown_recipients", malicious=True), False,
     (True, True)),
    ("08", "Exfiltration of business data by a former employee",
     # basic client contact data; intentions of the former employee unknown, further misuse cannot be ruled out
     BreachFacts("simple", (), "maximum", confidentiality="unknown_recipients", malicious=True), False,
     (True, False)),
    ("12", "Stolen paper files with sensitive data",
     # paper log book of a rehabilitation facility: identity and health data, no backup
     BreachFacts("sensitive", (), "maximum", confidentiality="unknown_recipients", availability="permanent",
                 malicious=True), False, (True, True)),
    ("13", "Postal mail mistake",
     # two customers received each other's order and bill; EDPB: risk low, Art. 34 not mandated
     BreachFacts("simple", (), "maximum", confidentiality="known_recipients"), False, (False, False)),
    ("14", "Highly confidential personal data sent by mail by mistake",
     BreachFacts("financial", (), "maximum", confidentiality="unknown_recipients"), False, (True, True)),
    ("15", "Personal data sent by mail by mistake",
     BreachFacts("simple", (), "maximum", confidentiality="known_recipients"), False, (False, False)),
    ("17", "Identity theft",
     # impostor passed weak caller verification and changed the billing e-mail address; reversed later
     BreachFacts("financial", (), "maximum", confidentiality="unknown_recipients", integrity="altered_recoverable",
                 malicious=True), False, (True, True)),
    ("18", "Email exfiltration",
     # salary data of employees exfiltrated through a compromised mailbox; EDPB: high risk
     BreachFacts("financial", (), "maximum", confidentiality="unknown_recipients", malicious=True), False,
     (True, True)),
]
# Case 16 (insurance letter in the wrong envelope) is not encoded: its conclusion table did not
# survive text extraction and the narrative gives no explicit Art. 33/34 conclusion.


@pytest.mark.parametrize("cid,title,facts,unint,expected", EDPB_CASES, ids=[c[0] for c in EDPB_CASES])
def test_edpb_01_2021_outcomes(cid, title, facts, unint, expected):
    sev = assess(facts)
    path = decide(sev, CaseContext(aware_at=AWARE, unintelligible=unint))
    assert (path.notify_sa, path.notify_subjects) == expected, f"Case {cid} {title}: SE={sev.score} {sev.band}"


def test_score_formula_is_dpc_times_ei_plus_cb():
    sev = assess(BreachFacts("financial", (), "maximum", confidentiality="unknown_recipients"))
    assert sev.score == 3 * 1.0 + 0.5
    assert sev.band == "high"


def test_dpc_is_clamped():
    sev = assess(BreachFacts("sensitive", (("vulnerable subjects", 1),), "maximum"))
    assert sev.dpc == 4


def test_invalid_values_rejected():
    with pytest.raises(ValueError):
        assess(BreachFacts("medical"))
    with pytest.raises(ValueError):
        assess(BreachFacts("simple", (("x", 2),)))


def test_borderline_score_escalates_but_keeps_provisional_decision():
    sev = assess(BreachFacts("behavioural", (), "maximum", availability="recoverable_delay", malicious=True))
    assert sev.score == 2.75 and sev.borderline
    path = decide(sev, CaseContext(aware_at=AWARE))
    assert path.decision == "escalate_to_dpo"
    assert path.provisional_decision == "notify_authority"


def test_34_3_a_unintelligible_blocks_communication_to_subjects():
    sev = assess(BreachFacts("sensitive", (), "maximum", confidentiality="unknown_recipients"))
    assert sev.band == "very_high"
    path = decide(sev, CaseContext(aware_at=AWARE, unintelligible=True))
    assert path.notify_sa and not path.notify_subjects


def test_clock_and_late_notification():
    sev = assess(BreachFacts("financial", (), "maximum", confidentiality="unknown_recipients"))
    late = decide(sev, CaseContext(aware_at=AWARE, now=datetime.fromisoformat("2026-10-23T12:00:00+02:00")))
    assert late.clock["hours_left"] == -3.0
    assert "late_notification_reasons_required" in late.flags


def test_processor_lag_flags_contract_review():
    sev = assess(BreachFacts("financial", (), "maximum", confidentiality="unknown_recipients", malicious=True))
    ctx = CaseContext(aware_at=datetime.fromisoformat("2026-10-21T15:00:00+02:00"), via_processor=True,
                      processor_detected_at=datetime.fromisoformat("2026-10-19T13:00:00+02:00"),
                      processor_notice_hours_contract=24)
    path = decide(sev, ctx)
    assert path.clock["processor_lag_hours"] == 50.0
    assert "processor_contract_breach_review" in path.flags
    assert path.clock["deadline"].startswith("2026-10-24T15:00")


def test_recipient_deletion_lowers_risk():
    base = BreachFacts("financial", (), "maximum", confidentiality="unknown_recipients")
    after = BreachFacts("financial", (), "maximum", confidentiality="unknown_recipients",
                        mitigations=("recipient_deletion_confirmed",))
    assert assess(base).band == "high"
    assert assess(after).band == "medium"
