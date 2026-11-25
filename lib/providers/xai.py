"""xAI (Grok) API provider client for GRIP Multi-Provider MoE.

Thin wrapper around xAI REST API (OpenAI-compatible) for the Grok 4.20 lineup.
API key fetched from macOS Keychain (grip-xai), cached after first retrieval.

Usage:
    from lib.providers.xai import XaiProvider

    provider = XaiProvider()
    text = provider.generate("Explain SOLID principles", temperature=0.3)

    # Use Grok 4.20 Reasoning for frontier reasoning tasks
    text = provider.generate("Analyse this proof", model="grok-4.20-0309-reasoning")
"""
from __future__ import annotations

import json
import subprocess
import time
import urllib.request
import urllib.error
from dataclasses import dataclass, field
from typing import Optional

_KEYCHAIN_SERVICE = "grip-xai"
_API_BASE = "https://api.x.ai/v1"
_DEFAULT_MODEL = "grok-4.20-0309-non-reasoning"
_GROK_MINI = "grok-4.20-0309-non-reasoning"  # no "mini" tier left; non-reasoning is the fast/cheap rung
_GROK_4 = "grok-4.20-0309-reasoning"

_MAX_RETRIES = 3
_INITIAL_BACKOFF_S = 1.0
_BACKOFF_MULTIPLIER = 2.0


@dataclass
class XaiResult:
    """Response from an xAI generate call."""
    text: str = ""
    model: str = ""
    finish_reason: str = ""
    usage: dict = field(default_factory=dict)


class XaiProviderError(Exception):
    """Raised when xAI API returns a non-retryable error."""


class XaiRateLimitError(XaiProviderError):
    """Raised when rate limit is hit after exhausting retries."""


class XaiProvider:
    """KISS xAI API client with keychain auth and rate-limit backoff."""

    def __init__(self, api_key: Optional[str] = None, model: str = _DEFAULT_MODEL):
        self._api_key = api_key
        self._model = model

    @property
    def api_key(self) -> str:
        if self._api_key is None:
            self._api_key = _fetch_keychain_key()
            if self._api_key is None:
                raise XaiProviderError(
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
    ) -> XaiResult:
        """Generate text from a prompt via xAI API.

        Args:
            prompt: User message content.
            model: Override model. Use 'grok-4.20-0309-reasoning' for frontier reasoning.
            temperature: Sampling temperature.
            max_tokens: Maximum tokens to generate.
            system: Optional system message.
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
        body = self._call_api("chat/completions", payload)
        return _parse_response(body, model)

    def generate_fast(
        self,
        prompt: str,
        *,
        max_tokens: int = 2048,
        system: Optional[str] = None,
    ) -> XaiResult:
        """Generate with the non-reasoning model — faster, lower cost."""
        return self.generate(
            prompt,
            model=_GROK_MINI,
            temperature=0.7,
            max_tokens=max_tokens,
            system=system,
        )

    def generate_frontier(
        self,
        prompt: str,
        *,
        max_tokens: int = 4096,
        system: Optional[str] = None,
    ) -> XaiResult:
        """Generate with Grok 4.20 Reasoning — frontier reasoning, highest quality."""
        return self.generate(
            prompt,
            model=_GROK_4,
            temperature=0.6,
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
                    raise XaiRateLimitError(
                        f"Rate limited after {_MAX_RETRIES + 1} attempts"
                    ) from e
                body = ""
                try:
                    body = e.read().decode("utf-8")
                except Exception:
                    pass
                raise XaiProviderError(
                    f"xAI API error {e.code}: {e.reason} {body}"
                ) from e

            except urllib.error.URLError as e:
                raise XaiProviderError(f"Network error: {e.reason}") from e

        raise XaiProviderError(f"Unexpected retry exhaustion: {last_error}")


def _fetch_keychain_key() -> Optional[str]:
    """Fetch xAI API key from macOS Keychain."""
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


def _parse_response(body: dict, model: str) -> XaiResult:
    """Extract text and metadata from xAI API response."""
    choices = body.get("choices", [])
    choice = choices[0] if choices else {}
    message = choice.get("message", {})
    usage = body.get("usage", {})
    return XaiResult(
        text=message.get("content", ""),
        model=model,
        finish_reason=choice.get("finish_reason", ""),
        usage={
            "prompt_tokens": usage.get("prompt_tokens", 0),
            "completion_tokens": usage.get("completion_tokens", 0),
            "total_tokens": usage.get("total_tokens", 0),
        },
    )
