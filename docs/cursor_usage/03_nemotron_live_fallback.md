# Cursor Agent session 03 (2026-10-23)

Tool: Cursor Agent CLI 2026.10.22-27dcf5f, headless (`agent -p --force`), repository root. Trigger: first live test with a real NVIDIA key.

## Prompt

Live test with a real NVIDIA key on 2026-10-23 showed that the model catalogue (GET /v1/models) lists models that return HTTP 404 "Function ... Not found for account" on chat/completions, and that the working Nemotron models are reasoning models whose reasoning tokens count against max_tokens (with max_tokens=400 nemotron-3.5-lightning returned empty content). Working ids: nvidia/nemotron-3.5-super-vl-120b-a12b (2.7 s), nvidia/nemotron-3.5-lightning-30b-a3b, nvidia/nemotron-3-nano-omni-30b-a3b-reasoning, nvidia/nemotron-3-ultra-550b-a55b (28 s). Returning 404: nemotron-3-super-120b-a12b, nemotron-nano-3-30b-a3b, llama-3.1-nemotron-ultra-253b-v1, llama-3.1-nemotron-70b-instruct, llama-3.1-nemotron-51b-instruct.

Change app/adapters/nemotron.py:
1. DEFAULTS large = nvidia/nemotron-3.5-super-vl-120b-a12b, small = nvidia/nemotron-3.5-lightning-30b-a3b. FALLBACKS large: [3.5-super-vl-120b, 3-ultra-550b-a55b, 3-super-120b-a12b]; small: [3.5-lightning-30b-a3b, 3-nano-omni-30b-a3b-reasoning, nemotron-nano-3-30b-a3b]. Update the date comment.
2. On HTTPError 404 from chat/completions: mark that model unavailable for this client, pick the next fallback for that size that is not marked unavailable, write trace event model_substituted {size, configured, used, reason: "http_404"} with model=used, and retry with the new model without consuming a retry and without sleeping. If no fallback is left, log model_unavailable and return None.
3. Reasoning budget: send max_tokens = max(requested, LLM_MIN_TOKENS) where LLM_MIN_TOKENS env default is 2000.
4. Model timeout: use LLM_TIMEOUT_SECONDS (default 60) for model calls instead of SOURCE_TIMEOUT_SECONDS.
5. If the response content is empty or whitespace, log llm_empty (with usage) and return None; do not record it.
6. Do not record or log reasoning_content.
Add the new env vars with defaults to .env.example (no secrets). Update the default model ids in README.md (AI and Model Usage section) and metadata.json. Add tests in tests/test_nemotron.py with the stub opener: 404 on the first model leads to substitution and a successful answer from the next fallback; empty content returns None and logs llm_empty; max_tokens sent is at least 2000. Keep existing tests passing. Standard library only. Never print or write any real key. Use only ASCII hyphens, never en or em dashes. Run python -m pytest -q and report.

## Result

Cursor changed app/adapters/nemotron.py, .env.example, README.md, metadata.json, tests/test_nemotron.py, and on its own also tests/test_pipeline.py and tests/test_meterless.py (the test fixtures now set an empty key instead of deleting it, because run.main loads .env with setdefault and would otherwise call the live API from tests). 63 tests passed. The agent's text report was not captured (console output was serialized as CLIXML by the nested PowerShell); the diff is the record.

