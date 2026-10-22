# Breach72

**Open Agent Hackathon 2026, Track 2: The Agent That Can Explain Why.** Team Archaios (Marek Porycki, Broker Business sp. z o.o.).

Breach72 investigates a personal data breach across an organisation's separate systems and tells the data
protection officer, within the 72-hour GDPR window, whether to notify the supervisory authority and the affected
people, and why, with every claim linked to its source record and legal basis.

Demo video: [`docs/OAH2026-Archaios-ExplainWhy-Demo.mp4`](docs/OAH2026-Archaios-ExplainWhy-Demo.mp4).
Deck: [`docs/Breach72_deck.pdf`](docs/Breach72_deck.pdf).

## Who it is for

Data protection officers and compliance leads in small and mid-sized organisations and public bodies. Under
GDPR Art. 33 they must notify the supervisory authority within 72 hours of becoming aware of a breach, and under
Art. 34 tell the affected people when the risk is high. The facts sit in different systems: the incident log, the
record of processing activities, the asset and encryption register and processor contracts.

## Quick start

Requirements: Python 3.11+. No third-party runtime packages.

```bash
python -m pip install -r requirements.txt          # pytest only
python data/generator.py                          # SYNTHETIC data (deterministic)
python -m pytest -q                               # 45 tests: rule set (EDPB 01/2021 cases) and pipeline
python run.py input_examples/case2_misdirected_email.json --backend dev --out out.json
python report.py out.json                         # HTML report next to the JSON
```

Backends:

| Backend | Use | Needs |
|---|---|---|
| `zetaris` | live queries through Zetaris; every response is recorded to `recordings/` | `.env` (see `.env.example`) |
| `replay` | default when `SAMPLE_MODE=true`: replays recorded live Zetaris responses; all agent logic runs for real | nothing |
| `dev` | local SQLite over the synthetic CSVs, for development; every output says so in `limitations` | nothing |

Docker: `docker build -t breach72 . && docker run --rm breach72 input_examples/case2_misdirected_email.json`.

## How a decision is made

1. **Planner** fixes the awareness moment (earliest reliable record if none is logged, flagged as assumed) and
   orders evidence questions by their impact on the decision.
2. **Investigator** answers them with vetted, parameterised SQL templates (`app/queries/templates.py`). No
   free-form text-to-SQL. Every finding carries provenance: backend, template, tables, rows, query hash, time.
3. **Evidence Auditor** vetoes any decisive claim without evidence and sends it back to the Planner (max 3
   iterations). It re-verifies key facts (number of people, encryption, disclosure, data categories) with a
   different template. Disagreeing sources open a conflict branch.
4. **Risk Assessor** is deterministic and unit-tested: ENISA 2013 severity `SE = DPC x EI + CB`, mapped to GDPR
   Art. 33(1), 33(2), 33(4), 33(5), 34(1), 34(3)(a) and 28(3)(f) (`app/rules/`).
5. **Reviewer & Drafter** checks the decision against the rule, writes the explanation (each line cites finding
   ids and legal text), drafts the Art. 33(3) notification and Art. 34 communication, lists open questions and
   escalates to the DPO when the score is borderline, sources conflict or the awareness time was assumed.

Output JSON: `run_id, status, decision, provisional_decision, risk, explanation, findings[evidence, verified_by,
confidence], legal_basis, draft_notification, open_questions, clock{aware_at, assumed, deadline, hours_left},
limitations, agents, trace_id, log_file, execution_time`. Errors: `{type, message, recoverable}`.
Trace: one JSONL file per run in `logs/`, written by the code path that acted, with a `model` field on every event.

## Test cases (synthetic)

| Case | Scenario | Outcome on the dev backend |
|---|---|---|
| 1 | Lost laptop, full-disk encryption, key separate | document internally (SE 1.0) |
| 2 | 1 200 records with national ID e-mailed to an unknown address | notify authority and individuals (SE 3.5) |
| 2b | Same, after the recipient confirmed deletion in writing | notify authority (SE 2.5) |
| 3 | Ransomware on HR server; log shows 2 GB outbound, IT report says no exfiltration | escalate to DPO (conflict, Art. 9, phased notification) |
| 4 | Breach at the payroll processor, reported 50 hours after detection | escalate; Art. 33(2) and contract review flags |
| 5 | Public listing of contact form data, discovery time not recorded | escalate; awareness time assumed |

The rule set is tested against 17 of the 18 worked cases of EDPB Guidelines 01/2021 (all except case 16,
whose conclusion table is not machine-readable in the published PDF), `tests/test_rules_edpb.py`.
Borderline scores (for example case 08, SE 2.0) still escalate to the DPO in the full pipeline.

## Sponsor technology

- **Zetaris**: the single governed SQL access point to the four source systems (incident PostgreSQL database,
  ROPA, asset register and processor contracts). The Investigator queries in place through vetted templates; the
  Auditor re-queries with a different template before approval. Responses are recorded for sample mode.
- **Meterless** (Python implementations of the Apache-2.0 reference architecture, engines `world-model` and
  `hmem`, commit `1742dec`; Markovian and Scout are not used):
  - *World Model* (`app/adapters/meterless_world.py`) is the shared case state. Every finding and every status
    change by the Evidence Auditor is an `assertFact` on the incident entity with provenance (agent, template,
    query hash); "no provenance, no write"; most-recent-wins keeps superseded versions in the append-only event
    log (`logs/.world/<run_id>.json`). Agents read the current finding for a claim through it, and the Reviewer's
    model prompt is built by `ContextBuilder` from the current facts.
  - *H-MEM* (`app/adapters/meterless_hmem.py`, `app/orchestration/org_memory.py`) keeps verified facts about the
    organisation's registers (ROPA, asset security, processor contracts) across cases, with the canonical hybrid
    retrieval score, feedback deltas and an append-only `ledger.jsonl` (`memory/<organisation>/`). At the start of
    a case the facts for the affected system are recalled; the Evidence Auditor re-runs the source template for
    each one: a match gives `helpful` feedback and promotion to `long_term`, a mismatch gives `wrong` feedback and a
    superseding record taken from the source. Memory never feeds the rule directly. Try it: run case 2 and then
    case 2b; the second run recalls two facts and re-verifies them (`org_memory` in the output, trace events
    `recall` and `memory_reverified`).
- **NVIDIA Nemotron** (hosted NVIDIA API): see below.
- **Cursor**: development workspace; project rules in `.cursor/rules/breach72.mdc`.

## AI and Model Usage

- NVIDIA Nemotron via `integrate.api.nvidia.com` (OpenAI-compatible chat completions), configured in `.env`:
  `MODEL_SMALL` for the Planner's investigation note, `MODEL_LARGE` for the Auditor's conflict note and the
  Reviewer's plain-language summary.
- Models never produce the decision. The Reviewer accepts a model summary only if every finding id it cites
  exists; otherwise it is discarded and `llm_output_rejected` is logged.
- Without `NVIDIA_API_KEY` no model is called; each skipped call is logged as `llm_skipped` with `model: null`.
- Development: the code was written with AI assistance (Anthropic Claude as a coding agent, Cursor). The author
  reviewed the rules and the legal mapping.

## Status (22 October 2026)

Done: five agents, rule set with tests, five synthetic cases, trace, HTML report, Zetaris adapter with timeout
(20 s), bounded retries, recording and replay, Meterless World Model (shared case state) and H-MEM (organisational
memory with re-verification). In progress before the deadline: live Zetaris sources and recordings, live Nemotron
calls.

## Pre-window declaration

No project code, data or prompts were written before 22 October 2026, 00:00 UTC. Prepared before the window: a
design document and the author's general GDPR compliance know-how. Before the window only accounts were created and
tools installed.

## Third-Party Code and Licences

- Python standard library; `pytest` (MIT) for tests.
- Meterless reference architectures (Apache-2.0, github.com/Meterless/Meterless, commit 1742dec): the World Model
  and H-MEM modules are Python ports of the TypeScript reference (id grammar, event fold, retrieval formula,
  feedback deltas, ledger); file headers name the source files.
- Zetaris local platform, used as a service under its own terms; not redistributed here.
- Public legal texts: GDPR; EDPB Guidelines 01/2021 and 9/2022; ENISA (2013) severity methodology.

## Limitations

- Decision support for a data protection officer, not legal advice.
- All data is synthetic (`data/SYNTHETIC.md`); no real personal data.
- Severity follows the ENISA 2013 method; national supervisory authority practice may differ.
- Category-to-severity mapping is a documented policy in `app/agents/risk_assessor.py`, not an official table.

Licence: Apache-2.0.
