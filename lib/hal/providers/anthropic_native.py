"""Anthropic native Messages API provider.

Direct access to Anthropic's Messages API for features not available
via OpenAI-compatible format: extended thinking, prompt caching,
tool use with native schema, and streaming.

Uses stdlib urllib — no anthropic SDK dependency.

Usage:
    provider = AnthropicProvider(api_key="sk-ant-...")
    response = provider.chat("Explain SOLID", model="claude-sonnet-4-6")
"""
import json
import re
import socket
import subprocess
import time
import urllib.error
import urllib.request
from typing import Optional

from lib.hal.types import ChatMessage, ChatResponse, ModelInfo, TokenUsage

_KEYCHAIN_CMD = ["security", "find-generic-password", "-w", "-s"]
_API_BASE = "https://api.anthropic.com/v1"
# Sonnet 4.6's id is undated. The dated spelling this used to carry,
# claude-sonnet-4-6-20250514, was never a real id. A 404 alone would not show
# that (it cannot tell an unknown id from one not enabled for this account); the
# evidence is that it appears in neither exhaustive /v1/models listing (the
# subscription token and the direct key, 12 ids each, has_more=false,
# 2026-09-24), which list claude-sonnet-4-6 undated, and that the vendor's model
# table gives Sonnet 4.6 no dated full id (Haiku 4.5 is the only row with one).
# The probe agreed: 404 like a nonsense control, where claude-sonnet-4-6
# answered 429 (recognised, rate-limited).
_DEFAULT_MODEL = "claude-sonnet-4-6"
_API_VERSION = "2023-06-01"
_MAX_RETRIES = 3
_INITIAL_BACKOFF_S = 1.0
_BACKOFF_MULTIPLIER = 2.0
_KEY_PATTERN = re.compile(r"(sk-ant-|sk-|Bearer )\S+")


def _fetch_keychain_key(service: str) -> Optional[str]:
    try:
        result = subprocess.run(
            [*_KEYCHAIN_CMD, service],
            capture_output=True, text=True, timeout=5,
        )
        return result.stdout.strip() if result.returncode == 0 else None
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None


class AnthropicError(Exception):
    """Non-retryable Anthropic API error."""


class AnthropicRateLimitError(AnthropicError):
    """Rate limit hit after exhausting retries."""


class AnthropicProvider:
    """Native Anthropic Messages API provider."""

    def __init__(self, api_key: Optional[str] = None, model: str = _DEFAULT_MODEL):
        self._api_key = api_key
        self._key_resolved = api_key is not None
        self._model = model
        self.name = "anthropic"

    @property
    def api_key(self) -> Optional[str]:
        if not self._key_resolved:
            self._api_key = _fetch_keychain_key("grip-anthropic-external")
            self._key_resolved = True
        return self._api_key

    def chat(
        self,
        prompt: str,
        *,
        model: Optional[str] = None,
        messages: Optional[list[ChatMessage]] = None,
        system: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 2048,
    ) -> ChatResponse:
        """Send a message via Anthropic Messages API."""
        model = model or self._model

        if messages:
            msg_list = [{"role": m.role, "content": m.content} for m in messages]
        else:
            msg_list = [{"role": "user", "content": prompt}]

        body: dict = {
            "model": model,
            "messages": msg_list,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if system:
            body["system"] = system

        start_ms = time.monotonic() * 1000
        data = self._request("POST", "/messages", body)
        latency_ms = time.monotonic() * 1000 - start_ms

        return _parse_response(data, "anthropic", latency_ms)

    def list_models(self) -> list[ModelInfo]:
        # Every anthropic/ row of config/model_catalog.yaml must appear here;
        # tests/test_model_onboarding_parity.py fails naming this list if not.
        # The ids are the ones /v1/models returned (12 ids, 2026-09-24), plus
        # claude-sonnet-5-5 and claude-haiku-5-5, which served on 2026-10-12
        # through Claude Code 2.1.296 (modelUsage keyed by each id, a bogus
        # control id refused) and through HAL's CCH signer.
        return [
            ModelInfo(id="claude-opus-5-5", provider="anthropic",
                      display_name="Opus 5.5", context_window=1_000_000),
            ModelInfo(id="claude-opus-5", provider="anthropic",
                      display_name="Opus 5", context_window=1_000_000),
            ModelInfo(id="claude-opus-4-8", provider="anthropic",
                      display_name="Opus 4.8", context_window=1_000_000),
            ModelInfo(id="claude-opus-4-7", provider="anthropic",
                      display_name="Opus 4.7", context_window=1_000_000),
            ModelInfo(id="claude-opus-4-6", provider="anthropic",
                      display_name="Opus 4.6", context_window=1_000_000),
            ModelInfo(id="claude-sonnet-5-5", provider="anthropic",
                      display_name="Sonnet 5.5", context_window=1_000_000),
            ModelInfo(id="claude-sonnet-5", provider="anthropic",
                      display_name="Sonnet 5", context_window=1_000_000),
            ModelInfo(id="claude-sonnet-4-6", provider="anthropic",
                      display_name="Sonnet 4.6", context_window=1_000_000),
            ModelInfo(id="claude-fable-5-2", provider="anthropic",
                      display_name="Fable 5.2", context_window=1_000_000),
            ModelInfo(id="claude-fable-5-1", provider="anthropic",
                      display_name="Fable 5.1", context_window=1_000_000),
            ModelInfo(id="claude-fable-5", provider="anthropic",
                      display_name="Fable 5", context_window=1_000_000),
            ModelInfo(id="claude-haiku-5-5", provider="anthropic",
                      display_name="Haiku 5.5", context_window=1_000_000),
            ModelInfo(id="claude-haiku-4-5-20251001", provider="anthropic",
                      display_name="Haiku 4.5", context_window=200_000),
        ]

    def health_check(self) -> bool:
        return self.api_key is not None

    def _request(self, method: str, path: str, body: Optional[dict] = None) -> dict:
        url = f"{_API_BASE}{path}"
        backoff = _INITIAL_BACKOFF_S

        for attempt in range(_MAX_RETRIES + 1):
            try:
                return self._do_request(method, url, body)
            except urllib.error.HTTPError as e:
                if e.code == 429 and attempt < _MAX_RETRIES:
                    time.sleep(backoff)
                    backoff *= _BACKOFF_MULTIPLIER
                    continue
                if e.code == 429:
                    raise AnthropicRateLimitError("Anthropic rate limited") from e
                error_body = _read_error_body(e)
                raise AnthropicError(f"Anthropic HTTP {e.code}: {error_body}") from e
            except (urllib.error.URLError, socket.timeout, TimeoutError) as e:
                if attempt < _MAX_RETRIES:
                    time.sleep(backoff)
                    backoff *= _BACKOFF_MULTIPLIER
                    continue
                raise AnthropicError(f"Anthropic connection failed: {e}") from e

        raise AnthropicError(f"Exhausted {_MAX_RETRIES} retries")

    def _do_request(self, method: str, url: str, body: Optional[dict]) -> dict:
        """Single HTTP request. The body's prose passes the redaction shield first;
        when it cannot, nothing is sent and this raises AnthropicError."""
        from . import shield_request
        try:
            req_data = json.dumps(shield_request(body)).encode() if body else None
        except OSError as exc:  # a failed scrub, or no shield to load: nothing is sent
            raise AnthropicError(f"Anthropic request not sent: {exc}") from exc
        headers = {
            "Content-Type": "application/json",
            "x-api-key": self.api_key or "",
            "anthropic-version": _API_VERSION,
            "User-Agent": "HAL/1.0",
        }
        req = urllib.request.Request(url, data=req_data, headers=headers, method=method)
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read().decode())
            data["_headers"] = dict(resp.headers)
            return data

    def __repr__(self) -> str:
        return f"AnthropicProvider(model={self._model!r})"


def _parse_response(data: dict, provider: str, latency_ms: float) -> ChatResponse:
    content_blocks = data.get("content", [])
    text = "".join(
        block.get("text", "") for block in content_blocks
        if block.get("type") == "text"
    )

    usage_raw = data.get("usage", {})
    inp = usage_raw.get("input_tokens", 0)
    out = usage_raw.get("output_tokens", 0)

    return ChatResponse(
        text=text,
        model=data.get("model", ""),
        provider=provider,
        finish_reason=data.get("stop_reason", "end_turn"),
        usage=TokenUsage(
            prompt_tokens=inp, completion_tokens=out, total_tokens=inp + out,
            cache_read_tokens=usage_raw.get("cache_read_input_tokens", 0),
            cache_creation_tokens=usage_raw.get("cache_creation_input_tokens", 0),
        ),
        latency_ms=latency_ms,
        raw=data,
    )


def _read_error_body(e: urllib.error.HTTPError) -> str:
    try:
        raw = e.read().decode("utf-8", errors="replace")[:500]
        return _KEY_PATTERN.sub("[REDACTED]", raw)
    except Exception:
        return ""
