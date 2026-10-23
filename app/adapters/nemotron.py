"""NVIDIA Nemotron through the hosted NVIDIA API (OpenAI-compatible chat completions).

Role split: MODEL_SMALL for planning text, MODEL_LARGE for evidence assessment and
explanations. Models never produce the decision; their output is validated by the
caller and discarded if it cites evidence that does not exist or contradicts the
decision.

Live mode (NVIDIA_API_KEY set):
- before the first call the configured model ids are checked against the live
  catalogue (GET /v1/models); a retired id is replaced by the first available id
  from FALLBACKS and the substitution is written to the trace (`model_substituted`);
- if chat/completions returns HTTP 404 for a model that is listed in the catalogue
  but not enabled for the account, that model is marked unavailable, the next
  FALLBACKS id is tried (no retry consumed, no sleep), and `model_substituted`
  is logged with reason http_404;
- max_tokens is raised to at least LLM_MIN_TOKENS (reasoning models count
  reasoning tokens against the budget); empty content is logged as llm_empty
  and not recorded; reasoning_content is never recorded or logged;
- LLM_THINKING defaults to off: the request body includes
  chat_template_kwargs.enable_thinking=false so the model does not spend the
  completion budget on reasoning; if the API returns HTTP 400 with that field
  present, llm_thinking_flag_rejected is logged once, the field is dropped for
  the rest of this client, and the call is resent immediately (no retry used);
- every successful response is recorded to recordings/nemotron/<prompt_hash>.json.

Without a key:
- with SAMPLE_MODE=true a recorded response for the identical prompt is replayed
  (`llm_replay`, `model` = the model that produced it, `recorded_at` kept);
- otherwise, or when no recording matches, the call is skipped (`llm_skipped`,
  model=null) and callers fall back to deterministic text. Nothing is generated locally.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from app.adapters.base import ROOT

RECORDINGS = ROOT / "recordings" / "nemotron"
# Model ids confirmed by live chat/completions on 2026-10-23 (catalogue alone is not
# enough: some listed ids return HTTP 404 Function ... Not found for account).
DEFAULTS = {
    "large": "nvidia/nemotron-3.5-super-vl-120b-a12b",
    "small": "nvidia/nemotron-3.5-lightning-30b-a3b",
}
FALLBACKS = {
    "large": [
        "nvidia/nemotron-3.5-super-vl-120b-a12b",
        "nvidia/nemotron-3-ultra-550b-a55b",
        "nvidia/nemotron-3-super-120b-a12b",
    ],
    "small": [
        "nvidia/nemotron-3.5-lightning-30b-a3b",
        "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning",
        "nvidia/nemotron-nano-3-30b-a3b",
    ],
}


_VOLATILE = [(re.compile(r"\bfact_[0-9a-f]{8,}\b"), "fact_*"),
             (re.compile(r"\b\d{8}T\d{6}-[0-9a-f]{6}\b"), "<run_id>")]


def _stable(text: str) -> str:
    """Remove per-run identifiers (World Model fact ids, run ids) so that the same case
    content produces the same prompt hash on every run; needed for SAMPLE_MODE replay."""
    for pat, repl in _VOLATILE:
        text = pat.sub(repl, text)
    return text


def prompt_hash(agent: str, size: str, system: str, user: str) -> str:
    blob = json.dumps([agent, size, _stable(system), _stable(user)], ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:20]


def _without_reasoning(payload: dict) -> dict:
    """Copy of the API payload with reasoning_content stripped (never record or log it)."""
    clean = json.loads(json.dumps(payload))
    for choice in clean.get("choices", []):
        msg = choice.get("message")
        if isinstance(msg, dict):
            msg.pop("reasoning_content", None)
    return clean


class NemotronClient:
    def __init__(self, trace=None, opener=None, recordings: Path | None = None):
        self.api_key = os.environ.get("NVIDIA_API_KEY", "").strip()
        if self.api_key.startswith("your-"):
            self.api_key = ""
        self.base_url = os.environ.get("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1").rstrip("/")
        self.models = {"large": os.environ.get("MODEL_LARGE") or DEFAULTS["large"],
                       "small": os.environ.get("MODEL_SMALL") or DEFAULTS["small"]}
        self.timeout = float(os.environ.get("LLM_TIMEOUT_SECONDS", "60"))
        self.min_tokens = int(os.environ.get("LLM_MIN_TOKENS", "800"))
        self.max_retries = int(os.environ.get("MAX_RETRIES", "2"))
        _thinking = os.environ.get("LLM_THINKING", "off").strip().lower()
        self.thinking = "on" if _thinking in ("on", "true", "1") else "off"
        self._send_thinking_off = self.thinking == "off"
        self.sample_mode = os.environ.get("SAMPLE_MODE", "true").lower() == "true"
        self.recordings = recordings or RECORDINGS
        self.trace = trace
        self._open = opener or urllib.request.urlopen
        self._catalogue_checked = False
        self._unavailable: set[str] = set()
        self._thinking_flag_rejected_logged = False

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    @property
    def mode(self) -> str:
        return "live" if self.enabled else ("replay" if self.sample_mode else "disabled")

    def _next_fallback(self, size: str) -> str | None:
        return next((m for m in FALLBACKS[size] if m not in self._unavailable), None)

    # ---- catalogue check -------------------------------------------------
    def _check_catalogue(self) -> None:
        self._catalogue_checked = True
        req = urllib.request.Request(self.base_url + "/models", method="GET",
                                     headers={"Authorization": f"Bearer {self.api_key}", "Accept": "application/json"})
        try:
            with self._open(req, timeout=self.timeout) as resp:
                ids = {m["id"] for m in json.loads(resp.read().decode("utf-8")).get("data", [])}
        except (urllib.error.URLError, TimeoutError, KeyError, json.JSONDecodeError) as exc:
            if self.trace:
                self.trace.event("nemotron", "catalogue_unavailable", {"error": str(exc)[:200]})
            return
        for size, wanted in list(self.models.items()):
            if wanted in ids and wanted not in self._unavailable:
                continue
            repl = next((m for m in FALLBACKS[size] if m in ids and m not in self._unavailable), None)
            if self.trace:
                self.trace.event("nemotron", "model_substituted" if repl else "model_unavailable",
                                 {"size": size, "configured": wanted, "used": repl}, model=repl)
            if repl:
                self.models[size] = repl

    # ---- recording / replay ----------------------------------------------
    def _record(self, ph: str, agent: str, size: str, model: str, system: str, user: str, payload: dict) -> None:
        self.recordings.mkdir(parents=True, exist_ok=True)
        rec = {"prompt_hash": ph, "backend": "nvidia-hosted-api", "agent": agent, "size": size, "model": model,
               "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
               "system": system, "user": user, "response": _without_reasoning(payload)}
        (self.recordings / f"{ph}.json").write_text(json.dumps(rec, indent=1, ensure_ascii=False), encoding="utf-8")

    def _replay(self, ph: str, agent: str) -> str | None:
        path = self.recordings / f"{ph}.json"
        if not path.exists():
            return None
        rec = json.loads(path.read_text(encoding="utf-8"))
        if rec.get("backend") != "nvidia-hosted-api" or rec.get("prompt_hash") != ph:
            if self.trace:
                self.trace.event(agent, "llm_replay_rejected", {"file": path.name,
                                                                "reason": "not a live NVIDIA recording"})
            return None
        text = rec["response"]["choices"][0]["message"]["content"]
        if self.trace:
            self.trace.event(agent, "llm_replay", {"prompt_hash": ph, "recorded_at": rec["recorded_at"],
                                                   "output_chars": len(text or "")}, model=rec["model"])
        return text

    # ---- call --------------------------------------------------------------
    def chat(self, agent: str, size: str, system: str, user: str, max_tokens: int = 600) -> str | None:
        ph = prompt_hash(agent, size, system, user)
        if not self.enabled:
            text = self._replay(ph, agent) if self.sample_mode else None
            if text is None and self.trace:
                self.trace.event(agent, "llm_skipped", {"reason": "llm_disabled", "mode": self.mode,
                                                        "wanted_model": self.models[size], "prompt_hash": ph},
                                 model=None)
            return text
        if not self._catalogue_checked:
            self._check_catalogue()
        tokens = max(max_tokens, self.min_tokens)
        model = self.models[size]
        attempt = 0
        while attempt <= self.max_retries:
            req_body = {"model": model, "temperature": 0.2, "max_tokens": tokens,
                        "messages": [{"role": "system", "content": system},
                                     {"role": "user", "content": user}]}
            if self._send_thinking_off:
                req_body["chat_template_kwargs"] = {"enable_thinking": False}
            body = json.dumps(req_body).encode()
            req = urllib.request.Request(self.base_url + "/chat/completions", data=body, method="POST",
                                         headers={"Authorization": f"Bearer {self.api_key}",
                                                  "Content-Type": "application/json", "Accept": "application/json"})
            t0 = time.perf_counter()
            try:
                with self._open(req, timeout=self.timeout) as resp:
                    payload = json.loads(resp.read().decode("utf-8"))
                text = payload["choices"][0]["message"].get("content")
                if not (text or "").strip():
                    if self.trace:
                        self.trace.event(agent, "llm_empty",
                                         {"prompt_hash": ph, "usage": payload.get("usage", {})}, model=model)
                    return None
                if self.trace:
                    self.trace.event(agent, "llm_call", {"attempt": attempt + 1, "prompt_hash": ph,
                                                         "latency_ms": int((time.perf_counter() - t0) * 1000),
                                                         "usage": payload.get("usage", {}),
                                                         "output_chars": len(text),
                                                         "thinking": self.thinking}, model=model)
                self._record(ph, agent, size, model, system, user, payload)
                return text
            except urllib.error.HTTPError as exc:
                if exc.code == 400 and self._send_thinking_off:
                    self._send_thinking_off = False
                    if self.trace and not self._thinking_flag_rejected_logged:
                        self._thinking_flag_rejected_logged = True
                        self.trace.event(agent, "llm_thinking_flag_rejected",
                                         {"prompt_hash": ph}, model=model)
                    continue
                if exc.code == 404:
                    configured = model
                    self._unavailable.add(model)
                    repl = self._next_fallback(size)
                    if self.trace:
                        self.trace.event(
                            "nemotron",
                            "model_substituted" if repl else "model_unavailable",
                            {"size": size, "configured": configured, "used": repl, "reason": "http_404"},
                            model=repl,
                        )
                    if not repl:
                        return None
                    self.models[size] = repl
                    model = repl
                    continue
                if self.trace:
                    self.trace.event(agent, "llm_error",
                                     {"attempt": attempt + 1, "error": str(exc)[:200]}, model=model)
                if attempt < self.max_retries:
                    time.sleep(min(2 ** (attempt + 1), 8))
                attempt += 1
            except (urllib.error.URLError, TimeoutError, KeyError, IndexError, json.JSONDecodeError) as exc:
                if self.trace:
                    self.trace.event(agent, "llm_error", {"attempt": attempt + 1, "error": str(exc)[:200]},
                                     model=model)
                if attempt < self.max_retries:
                    time.sleep(min(2 ** (attempt + 1), 8))
                attempt += 1
        return None
