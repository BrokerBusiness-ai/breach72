"""Required content of a notification under GDPR Art. 33(3) and a communication under Art. 34(2).

The draft is assembled from verified findings only. A field without verified
evidence is left as an explicit gap, never filled with a guess.
"""
from __future__ import annotations

ART33_3 = {
    "a_nature": "Art. 33(3)(a): nature of the breach, categories and approximate number of data subjects and records",
    "b_contact": "Art. 33(3)(b): name and contact details of the DPO or other contact point",
    "c_consequences": "Art. 33(3)(c): likely consequences of the breach",
    "d_measures": "Art. 33(3)(d): measures taken or proposed, including mitigation",
}
ART34_2 = ("a_nature", "b_contact", "c_consequences", "d_measures")

GAP = "[GAP: no verified evidence yet]"


def build_draft(fields: dict[str, str | None], notify_subjects: bool) -> dict:
    sa = {k: (fields.get(k) or GAP) for k in ART33_3}
    gaps = [ART33_3[k] for k, v in sa.items() if v == GAP]
    draft = {"to_supervisory_authority": sa, "missing": gaps, "complete": not gaps}
    if notify_subjects:
        draft["to_data_subjects"] = {k: sa[k] for k in ART34_2}
        draft["to_data_subjects"]["language"] = "clear and plain language (Art. 34(2))"
    return draft
