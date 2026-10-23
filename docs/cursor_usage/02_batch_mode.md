# Cursor Agent session 02 (2026-10-23)

Tool: Cursor Agent CLI 2026.10.22-27dcf5f, headless (`agent -p --force`), repository root.

## Prompt

Add a batch mode to run.py: `python run.py --all [--backend X] [--out-dir DIR]` runs the input_examples cases in this fixed order: case2_misdirected_email, case2b_recipient_deleted, case1_lost_encrypted_laptop, case3_ransomware_hr, case4_processor_late_notice, case5_no_detection_date. Before the first case it points HMEM_DIR to a fresh empty temporary directory (unless --keep-memory is given), so that organisational memory starts empty exactly as in a clean clone and recorded model prompts replay. With --out-dir it writes one JSON per case named after the input file. It prints a one-line summary per case (incident_id, decision, status) and returns 0 only if every case has status ok. Keep the existing single-case usage working (case becomes an optional positional argument; error if neither case nor --all is given). Update the usage docstring at the top of run.py and the run commands in README.md. Add a test in tests/test_pipeline.py that runs main(["--all", "--backend", "dev", "--out-dir", tmp]) and checks 6 output files and the decisions for case 2 (notify_authority_and_individuals) and 2b (notify_authority). Standard library only. Do not touch app/rules. Use only ASCII hyphens, never en or em dashes. Run python -m pytest -q and report.

## Agent report

Batch mode is in place. `python -m pytest -q`: **60 passed** in 4.10s.

**`run.py --all`**
- Runs the six cases in the fixed order (2 Ôćĺ 2b Ôćĺ 1 Ôćĺ 3 Ôćĺ 4 Ôćĺ 5)
- Sets `HMEM_DIR` to a fresh empty temp dir before the first case (unless `--keep-memory`)
- Prints `incident_id decision status` per case; exit 0 only if every status is `ok`
- `--out-dir` writes one JSON per case named after the input file
- Single-case usage unchanged; errors if neither a case path nor `--all` is given

**Also:** README quick-start line for `--all`; `test_batch_all_writes_six_outputs` in `tests/test_pipeline.py`.

## Result

run.py, README.md, tests/test_pipeline.py changed; 60 tests passed. Check: `python run.py --all --backend dev` gives case 2 notify_authority_and_individuals, 2b notify_authority, 1 document_internally, 3-5 escalate_to_dpo.

