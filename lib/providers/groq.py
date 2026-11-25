"""Groq API provider client for GRIP Multi-Provider MoE.

Thin wrapper around Groq REST API. GPT-OSS 120B answers by default, and
generate_reasoning() uses Qwen 3.8 27B; both are on Groq's live roster.
API key fetched from macOS Keychain (grip-groq), cached after first retrieval.

Usage:
    from lib.providers.groq import GroqProvider

    provider = GroqProvider()
    text = provider.generate("Explain SOLID principles", temperature=0.3)

    # A reasoning model for reasoning tasks
    text = provider.generate_reasoning("Analyse this proof")
"""
from __future__ import annotations

import json
import subprocess
import time
import urllib.request
import urllib.error
from dataclasses import dataclass, field
from typing import Optional

_KEYCHAIN_SERVICE = "grip-groq"
_API_BASE = "https://api.groq.com/openai/v1"
_DEFAULT_MODEL = "openai/gpt-oss-120b"
#: The reasoning default. Groq decommissioned qwen/qwen3-32b on 2026-07-17, and
#: Qwen 3.8 27B is on its live roster (GET /v1/models, read 2026-10-17).
_QWEN_REASONING = "qwen/qwen3.8-27b"
_LLAMA4_SCOUT = "meta-llama/llama-4-scout-17b-16e-instruct"

_MAX_RETRIES = 3
_INITIAL_BACKOFF_S = 1.0
_BACKOFF_MULTIPLIER = 2.0


@dataclass
class GroqResult:
    """Response from a Groq generate call."""
    text: str = ""
    model: str = ""
    finish_reason: str = ""
    usage: dict = field(default_factory=dict)


class GroqProviderError(Exception):
    """Raised when Groq API returns a non-retryable error."""


class GroqRateLimitError(GroqProviderError):
    """Raised when rate limit is hit after exhausting retries."""


class GroqProvider:
    """KISS Groq API client with keychain auth and rate-limit backoff."""

    def __init__(self, api_key: Optional[str] = None, model: str = _DEFAULT_MODEL):
        self._api_key = api_key
        self._model = model

    @property
    def api_key(self) -> str:
        if self._api_key is None:
            self._api_key = _fetch_keychain_key()
            if self._api_key is None:
                raise GroqProviderError(
                    f"No API key found in Keychain service '{_KEYCHAIN_SERVICE}' "
                    "and none provided explicitly"
                )
        return self._api_key

    @property
    def model(self) -> str:
        return self._model

    def generate(
        self,
        prompt: str,
        *,
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 2048,
        system: Optional[str] = None,
        reasoning_effort: Optional[str] = None,
    ) -> GroqResult:
        """Generate text from a prompt via Groq API.

        Args:
            prompt: User message content.
            model: Override model. generate_reasoning() picks a reasoning model.
            temperature: Sampling temperature.
            max_tokens: Maximum tokens to generate.
            system: Optional system message.
            reasoning_effort: Sent only when set; the model must accept it.
        """
        model = model or self._model
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if reasoning_effort:
            payload["reasoning_effort"] = reasoning_effort
        body = self._call_api("chat/completions", payload)
        return _parse_response(body, model)

    def generate_reasoning(
        self,
        prompt: str,
        *,
        model: Optional[str] = None,
        max_tokens: int = 4096,
        system: Optional[str] = None,
    ) -> GroqResult:
        """Generate with a reasoning model: the caller's ``model``, or Qwen 3.8 27B.

        It used to send a fixed Qwen3 32B whatever its caller configured, so the
        multi-LLM council's Qwen member sent a model Groq had retired (GRIP#8514).
        It asks for medium reasoning effort, which Qwen 3.8 27B and both GPT-OSS
        models accept. Without it Qwen 3.8 does not reason: Groq's reasoning guide
        (console.groq.com/docs/reasoning, read 2026-10-17) says of its default
        setting, "On Groq, Qwen 3.8 27B does not return reasoning tokens".
        """
        return self.generate(
            prompt,
            model=model or _QWEN_REASONING,
            temperature=0.6,
            max_tokens=max_tokens,
            system=system,
            reasoning_effort="medium",
        )

    def generate_llama4(
        self,
        prompt: str,
        *,
        max_tokens: int = 4096,
        system: Optional[str] = None,
    ) -> GroqResult:
        """Generate with Llama 4 Scout 17B.

        Groq retired this model on 2026-08-30 and serves no Llama model now, so
        this call fails. What should replace it is still open (GRIP#8514).
        """
        return self.generate(
            prompt,
            model=_LLAMA4_SCOUT,
            temperature=0.7,
            max_tokens=max_tokens,
            system=system,
        )

    def _call_api(self, endpoint: str, payload: dict) -> dict:
        """Make an API call with exponential backoff on rate limits."""
        url = f"{_API_BASE}/{endpoint}"
        from . import shield_request  # guard, then scrub, before any attempt
        data = json.dumps(shield_request(payload, to=url)).encode("utf-8")

        backoff = _INITIAL_BACKOFF_S
        last_error = None

        for attempt in range(_MAX_RETRIES + 1):
            try:
                req = urllib.request.Request(
                    url,
                    data=data,
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": f"Bearer {self.api_key}",
                        "User-Agent": "GRIP/6.0",
                    },
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=60) as resp:
                    return json.loads(resp.read().decode("utf-8"))

            except urllib.error.HTTPError as e:
                last_error = e
                if e.code == 429 and attempt < _MAX_RETRIES:
                    time.sleep(backoff)
                    backoff *= _BACKOFF_MULTIPLIER
                    continue
                if e.code == 429:
                    raise GroqRateLimitError(
                        f"Rate limited after {_MAX_RETRIES + 1} attempts"
                    ) from e
                body = ""
                try:
                    body = e.read().decode("utf-8")
                except Exception:
                    pass
                raise GroqProviderError(
                    f"Groq API error {e.code}: {e.reason} {body}"
                ) from e

            except urllib.error.URLError as e:
                raise GroqProviderError(f"Network error: {e.reason}") from e

        raise GroqProviderError(f"Unexpected retry exhaustion: {last_error}")


def _fetch_keychain_key() -> Optional[str]:
    """Fetch Groq API key from macOS Keychain."""
    try:
        result = subprocess.run(
            ["security", "find-generic-password", "-s", _KEYCHAIN_SERVICE, "-w"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return None


def _parse_response(body: dict, model: str) -> GroqResult:
    """Extract text and metadata from Groq API response."""
    choices = body.get("choices", [])
    choice = choices[0] if choices else {}
    message = choice.get("message", {})
    usage = body.get("usage", {})
    return GroqResult(
        text=message.get("content", ""),
        model=model,
        finish_reason=choice.get("finish_reason", ""),
        usage={
            "prompt_tokens": usage.get("prompt_tokens", 0),
            "completion_tokens": usage.get("completion_tokens", 0),
            "total_tokens": usage.get("total_tokens", 0),
        },
    )
