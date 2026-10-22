# Breach72

**Open Agent Hackathon 2026, Track 2: The Agent That Can Explain Why.** Team Archaios.

Breach72 investigates a personal data breach across an organisation's separate systems and tells the data
protection officer, within the 72-hour GDPR window, whether to notify the supervisory authority and the affected
people, and why, with every claim linked to its source record and legal basis.

> Status: under construction (build window 22-27 October 2026). Full documentation follows.

## Quick start

```bash
python -m pytest -q                                                   # rule set and pipeline tests
python data/generator.py                                              # regenerate SYNTHETIC data
python run.py input_examples/case2_misdirected_email.json --backend dev
```

Backends: `zetaris` (live, needs `.env`), `replay` (SAMPLE_MODE: replays recorded live Zetaris responses),
`dev` (local SQLite over the synthetic CSVs, for development only; outputs say so).

## How a decision is made

1. **Planner** fixes the awareness moment and orders evidence questions by decision impact.
2. **Investigator** answers them with vetted SQL templates (`app/queries/templates.py`) executed through Zetaris.
3. **Evidence Auditor** vetoes claims without evidence and re-verifies key facts with a different template.
   Disagreeing sources open a conflict branch and escalate to the DPO.
4. **Risk Assessor** (deterministic, unit-tested): ENISA 2013 severity `SE = DPC x EI + CB`, mapped to
   GDPR Art. 33 and 34. Tested against EDPB Guidelines 01/2021 cases (`tests/test_rules_edpb.py`).
5. **Reviewer & Drafter** checks consistency, writes the explanation and the Art. 33(3) / Art. 34 drafts.

Language models (NVIDIA Nemotron) explain and assess evidence; they never produce the decision.

Decision support, not legal advice. All data is synthetic (`data/SYNTHETIC.md`). Licence: Apache-2.0.
