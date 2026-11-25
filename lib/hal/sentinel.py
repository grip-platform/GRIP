"""HAL Limit Sentinel — Zero limits through proactive detection and auto-failover.

Monitors provider utilisation via response headers, tracks limit state per
provider, and auto-routes to the next available provider before limits hit.

The user NEVER sees "You've hit your limit."

Usage:
    from lib.hal.sentinel import LimitSentinel

    sentinel = LimitSentinel(registry)
    response = sentinel.dispatch("anthropic/claude-opus-4-7", "Hello")
    # If Anthropic is near limit, transparently routes to Groq/Gemini/Ollama
"""
import json
import os
import threading
import time
from typing import Optional

from lib.hal.types import ChatResponse

from lib.hal.home import HAL_HOME as _HAL_HOME
_STATE_PATH = os.path.join(_HAL_HOME, "state", "hal", "limits.json")

# Default thresholds
_PROACTIVE_THRESHOLD = 0.85   # Auto-switch at 85% utilisation
_WARNING_THRESHOLD = 0.70     # Alert at 70%
_COOLDOWN_BASE_S = 60.0       # Base cooldown after 429
_COOLDOWN_MAX_S = 3600.0      # Max cooldown (1 hour)
_COOLDOWN_MULTIPLIER = 2.0    # Exponential backoff


class LimitSentinel:
    """Monitors limits across all providers and auto-routes around them."""

    def __init__(
        self,
        registry: "ProviderRegistry",
        proactive_threshold: float = _PROACTIVE_THRESHOLD,
        warning_threshold: float = _WARNING_THRESHOLD,
    ):
        self._registry = registry
        self._proactive = proactive_threshold
        self._warning = warning_threshold
        self._state: dict[str, ProviderLimitState] = {}
        self._lock = threading.Lock()
        self._load_state()

    def dispatch(
        self,
        model_address: str,
        prompt: str,
        fallback_chain: Optional[list[str]] = None,
        **kwargs,
    ) -> ChatResponse:
        """Route query with limit-aware failover.

        1. Pre-check: is the target provider near its limit?
        2. If yes: route to best available alternative
        3. On 429/402: catch, record, retry on next provider
        4. Post-response: update utilisation from headers
        """
        from lib.hal.providers.openai_compat import OpenAICompatError
        from lib.hal.registry import parse_model_address

        provider_name, model_name = parse_model_address(model_address)

        # Build attempt chain: requested provider first, then fallbacks
        chain = self._build_chain(provider_name, fallback_chain)
        errors = []

        for attempt_provider in chain:
            provider = self._registry.get(attempt_provider)
            if not provider:
                continue

            # Pre-check: skip providers near their limit
            if self._is_near_limit(attempt_provider):
                errors.append(f"{attempt_provider}: near limit, skipped")
                continue

            # Resolve model for this provider
            attempt_model = model_name if attempt_provider == provider_name else self._default_model(attempt_provider)
            if not attempt_model:
                continue

            try:
                response = provider.chat(prompt, model=attempt_model, **kwargs)
                self._post_response(attempt_provider, response)
                return response
            except OpenAICompatError as e:
                self._on_error(attempt_provider, e)
                errors.append(f"{attempt_provider}: {e}")
                continue

        raise RuntimeError(
            f"All providers exhausted or at limit: {'; '.join(errors)}"
        )

    def get_status(self) -> dict[str, dict]:
        """Return current limit state for all tracked providers."""
        with self._lock:
            return {
                name: state.to_dict()
                for name, state in self._state.items()
            }

    def is_available(self, provider_name: str) -> bool:
        """Check if a provider is currently available (not at limit)."""
        return not self._is_near_limit(provider_name)

    def reset(self, provider_name: str) -> None:
        """Manually reset a provider's limit state (e.g., after credit top-up)."""
        with self._lock:
            if provider_name in self._state:
                self._state[provider_name] = ProviderLimitState()
            self._save_state()

    def _build_chain(self, primary: str, fallback: Optional[list[str]]) -> list[str]:
        """Build ordered provider chain: primary -> fallbacks -> all others."""
        chain = []
        if primary:
            chain.append(primary)

        if fallback:
            chain.extend(p.split("/")[0] for p in fallback if p.split("/")[0] not in chain)
        else:
            routing = self._registry.routing
            chain.extend(
                p.split("/")[0] for p in routing.fallback_chain
                if p.split("/")[0] not in chain
            )

        # Add any registered provider not yet in chain
        for info in self._registry.list_providers():
            if info.name not in chain:
                chain.append(info.name)

        return chain

    def _is_near_limit(self, provider_name: str) -> bool:
        """Check if provider is near its limit (proactive detection)."""
        with self._lock:
            state = self._state.get(provider_name)
            if not state:
                return False

            now = time.time()

            # Check cooldown from previous 429
            if state.cooldown_until and now < state.cooldown_until:
                return True

            # Check hard rejection
            if state.status == "rejected":
                # Recover when the rejection window has elapsed. resets_at is set
                # only from Anthropic unified-ratelimit headers; a 402/quota
                # rejection (_on_error) sets a finite cooldown_until and never
                # resets_at, so honour EITHER — else a non-Anthropic provider
                # rejected once is skipped forever (its cooldown lapses but
                # resets_at stays None).
                resets_reached = state.resets_at is not None and now >= state.resets_at
                cooldown_lapsed = state.cooldown_until is not None and now >= state.cooldown_until
                if resets_reached or cooldown_lapsed:
                    state.status = "allowed"
                    state.utilisation = 0.0
                    return False
                return True

            # Proactive: check utilisation threshold
            if state.utilisation >= self._proactive:
                return True

            return False

    def _post_response(self, provider_name: str, response: ChatResponse) -> None:
        """Extract limit info from response headers and update state."""
        raw = response.raw or {}
        headers = raw.get("_headers", {})
        if not headers:
            return

        with self._lock:
            state = self._get_or_create(provider_name)
            _update_from_headers(state, headers)
            self._save_state()

    def _on_error(self, provider_name: str, error: Exception) -> None:
        """Handle limit-related errors."""
        error_str = str(error).lower()
        with self._lock:
            state = self._get_or_create(provider_name)

            if "429" in error_str or "rate" in error_str:
                state.consecutive_429s += 1
                state.last_429_at = time.time()
                backoff = min(
                    _COOLDOWN_BASE_S * (_COOLDOWN_MULTIPLIER ** (state.consecutive_429s - 1)),
                    _COOLDOWN_MAX_S,
                )
                state.cooldown_until = time.time() + backoff

            elif "402" in error_str or "credit" in error_str or "payment" in error_str:
                state.status = "rejected"
                state.cooldown_until = time.time() + _COOLDOWN_MAX_S

            elif "quota" in error_str or "limit" in error_str:
                state.status = "rejected"
                retry_after = _extract_retry_after(error_str)
                state.cooldown_until = time.time() + (retry_after or _COOLDOWN_BASE_S)

            self._save_state()

    def _default_model(self, provider_name: str) -> str:
        """Get default model for a provider from config."""
        config = self._registry._configs.get(provider_name)
        if config and config.models:
            return config.models[0]
        return ""

    def _get_or_create(self, name: str) -> "ProviderLimitState":
        if name not in self._state:
            self._state[name] = ProviderLimitState()
        return self._state[name]

    def _load_state(self) -> None:
        """Load persistent limit state from disk."""
        if not os.path.exists(_STATE_PATH):
            return
        try:
            with open(_STATE_PATH, "r") as f:
                data = json.load(f)
            with self._lock:
                for name, raw in data.items():
                    self._state[name] = ProviderLimitState.from_dict(raw)
        except (json.JSONDecodeError, OSError, KeyError):
            pass

    def _save_state(self) -> None:
        """Persist limit state to disk."""
        os.makedirs(os.path.dirname(_STATE_PATH), exist_ok=True)
        try:
            data = {name: s.to_dict() for name, s in self._state.items()}
            with open(_STATE_PATH, "w") as f:
                json.dump(data, f, indent=2)
        except OSError:
            pass


class ProviderLimitState:
    """Mutable limit state for a single provider."""

    def __init__(self) -> None:
        self.utilisation: float = 0.0
        self.status: str = "allowed"
        self.resets_at: Optional[float] = None
        self.cooldown_until: Optional[float] = None
        self.consecutive_429s: int = 0
        self.last_429_at: Optional[float] = None
        self.rpm_remaining: Optional[int] = None
        self.tpm_remaining: Optional[int] = None

    def to_dict(self) -> dict:
        return {
            "utilisation": self.utilisation,
            "status": self.status,
            "resets_at": self.resets_at,
            "cooldown_until": self.cooldown_until,
            "consecutive_429s": self.consecutive_429s,
            "last_429_at": self.last_429_at,
            "rpm_remaining": self.rpm_remaining,
            "tpm_remaining": self.tpm_remaining,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ProviderLimitState":
        state = cls()
        state.utilisation = data.get("utilisation", 0.0)
        state.status = data.get("status", "allowed")
        state.resets_at = data.get("resets_at")
        state.cooldown_until = data.get("cooldown_until")
        state.consecutive_429s = data.get("consecutive_429s", 0)
        state.last_429_at = data.get("last_429_at")
        state.rpm_remaining = data.get("rpm_remaining")
        state.tpm_remaining = data.get("tpm_remaining")
        return state


def _update_from_headers(state: ProviderLimitState, headers: dict) -> None:
    """Update limit state from response headers."""
    # Anthropic utilisation headers (verified from CC source)
    for window in ("5h", "7d"):
        util_key = f"anthropic-ratelimit-unified-{window}-utilization"
        reset_key = f"anthropic-ratelimit-unified-{window}-reset"

        if util_key in headers:
            util = float(headers[util_key])
            state.utilisation = max(state.utilisation, util)

        if reset_key in headers:
            state.resets_at = float(headers[reset_key])

    # Standard rate limit headers (OpenAI format, used by most providers)
    if "x-ratelimit-remaining-requests" in headers:
        state.rpm_remaining = int(headers["x-ratelimit-remaining-requests"])
    if "x-ratelimit-remaining-tokens" in headers:
        state.tpm_remaining = int(headers["x-ratelimit-remaining-tokens"])

    # Reset consecutive 429 count on successful response
    state.consecutive_429s = 0
    state.cooldown_until = None
    state.status = "allowed"


def _extract_retry_after(error_text: str) -> Optional[float]:
    """Try to extract a retry-after value from error text."""
    import re
    match = re.search(r"retry.?after[:\s]+(\d+)", error_text, re.IGNORECASE)
    if match:
        return float(match.group(1))
    return None
