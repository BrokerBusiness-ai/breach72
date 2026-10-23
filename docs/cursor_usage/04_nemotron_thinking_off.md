# Cursor Agent session 04 (2026-10-23)

Tool: Cursor Agent CLI 2026.10.22-27dcf5f, headless (`agent -p --force`), repository root.

## Prompt

Live measurement on 2026-10-23: with reasoning on, the Reviewer prompt used all 2000 completion tokens on reasoning (llm_empty) or timed out at 60 s. Sending "chat_template_kwargs": {"enable_thinking": false} in the chat/completions body gives 0 reasoning tokens and answers in 1-3 s on both nvidia/nemotron-3.5-super-vl-120b-a12b and nvidia/nemotron-3.5-lightning-30b-a3b.
Change app/adapters/nemotron.py: add env LLM_THINKING (default "off"). When off, include "chat_template_kwargs": {"enable_thinking": false} in the request body. If the API answers HTTP 400 while that field is present, log llm_thinking_flag_rejected once, drop the field for the rest of this client and resend immediately (no retry consumed). Record the thinking setting in the llm_call trace data ("thinking": "off" or "on"). Keep LLM_MIN_TOKENS but change its default to 800. Add LLM_THINKING=off to .env.example and one sentence to the AI and Model Usage section of README.md. Add tests with the stub opener: the body contains the flag by default; a 400 with the flag leads to one resend without it; LLM_THINKING=on omits it. Never print or write any real key. Use only ASCII hyphens. Run python -m pytest -q and report.

## Agent report

**66 passed** in 2.81s. Case2 smoke OK.

**Changes**
- `LLM_THINKING` (default `off`): body gets `chat_template_kwargs: {enable_thinking: false}`; on HTTP 400 with that field, log `llm_thinking_flag_rejected` once, drop it for the client, resend without consuming a retry.
- `llm_call` trace includes `thinking: off|on`.
- `LLM_MIN_TOKENS` default **800**; `.env.example` has `LLM_THINKING=off`; README AI section updated.
- Tests: flag present by default; 400 Ôćĺ one resend without flag; `LLM_THINKING=on` omits it.


