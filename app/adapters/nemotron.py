"""NVIDIA Nemotron through the hosted NVIDIA API (OpenAI-compatible chat completions).

Role split: MODEL_SMALL for planning text, MODEL_LARGE for evidence assessment and
explanations. Models never produce the decision; their output is validated by the
caller and discarded if it cites evidence that does not exist.

Without NVIDIA_API_KEY the client is disabled: callers fall back to deterministic
text and the trace records model=null with reason "llm_disabled". Nothing is faked.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request


class NemotronClient:
    def __init__(self, trace=None, opener=None):
        self.api_key = os.environ.get("NVIDIA_API_KEY", "").strip()
        if self.api_key.startswith("your-"):
            self.api_key = ""
        self.base_url = os.environ.get("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1").rstrip("/")
        self.models = {"large": os.environ.get("MODEL_LARGE", "nvidia/llama-3.3-nemotron-super-49b-v1.5"),
                       "small": os.environ.get("MODEL_SMALL", "nvidia/nvidia-nemotron-nano-9b-v2")}
        self.timeout = float(os.environ.get("SOURCE_TIMEOUT_SECONDS", "20"))
        self.max_retries = int(os.environ.get("MAX_RETRIES", "2"))
        self.trace = trace
        self._open = opener or urllib.request.urlopen

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    def chat(self, agent: str, size: str, system: str, user: str, max_tokens: int = 600) -> str | None:
        model = self.models[size]
        if not self.enabled:
            if self.trace:
                self.trace.event(agent, "llm_skipped", {"reason": "llm_disabled", "wanted_model": model}, model=None)
            return None
        body = json.dumps({"model": model, "temperature": 0.2, "max_tokens": max_tokens,
                           "messages": [{"role": "system", "content": system},
                                        {"role": "user", "content": user}]}).encode()
        req = urllib.request.Request(self.base_url + "/chat/completions", data=body, method="POST",
                                     headers={"Authorization": f"Bearer {self.api_key}",
                                              "Content-Type": "application/json", "Accept": "application/json"})
        for attempt in range(self.max_retries + 1):
            t0 = time.perf_counter()
            try:
                with self._open(req, timeout=self.timeout) as resp:
                    payload = json.loads(resp.read().decode("utf-8"))
                text = payload["choices"][0]["message"]["content"]
                if self.trace:
                    self.trace.event(agent, "llm_call", {"attempt": attempt + 1,
                                                         "latency_ms": int((time.perf_counter() - t0) * 1000),
                                                         "usage": payload.get("usage", {}),
                                                         "output_chars": len(text or "")}, model=model)
                return text
            except (urllib.error.URLError, TimeoutError, KeyError, json.JSONDecodeError) as exc:
                if self.trace:
                    self.trace.event(agent, "llm_error", {"attempt": attempt + 1, "error": str(exc)[:200]},
                                     model=model)
                if attempt < self.max_retries:
                    time.sleep(min(2 ** (attempt + 1), 8))
        return None
