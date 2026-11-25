"""CC Rate Limit Harness for RSI Throttling (#1150).

Detects rate limit signals from the Claude Code environment and provides
adaptive backoff for RSI loops. Rate limit state is persisted to disk
so it survives across tool calls within a session.

Usage:
    from lib.cc_rate_limiter import check_rate_limit, record_rate_limit, get_backoff_delay

    # In RSI loop before each API-heavy operation:
    limit_info = check_rate_limit()
    if limit_info["throttled"]:
        time.sleep(limit_info["delay_seconds"])
"""
from __future__ import annotations

import json
import os
import random
import time
from pathlib import Path

CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", Path.home() / ".claude"))
RATE_LIMIT_STATE = CLAUDE_DIR / "state" / "rsi" / "rate-limit-state.json"

# Backoff parameters
BASE_DELAY = 2.0       # seconds
MAX_DELAY = 120.0      # seconds
JITTER_FACTOR = 0.25   # +/- 25% random jitter
COOLDOWN_WINDOW = 300.0  # 5 minutes — events older than this are forgotten


def _load_state() -> dict:
    """Load rate limit state from disk."""
    if RATE_LIMIT_STATE.exists():
        try:
            return json.loads(RATE_LIMIT_STATE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {"events": [], "consecutive_hits": 0, "last_hit_ts": 0.0}


def _save_state(state: dict) -> None:
    """Persist rate limit state to disk."""
    RATE_LIMIT_STATE.parent.mkdir(parents=True, exist_ok=True)
    RATE_LIMIT_STATE.write_text(
        json.dumps(state, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _prune_old_events(state: dict, now: float) -> dict:
    """Remove events outside the cooldown window."""
    cutoff = now - COOLDOWN_WINDOW
    state["events"] = [ts for ts in state["events"] if ts > cutoff]
    # Reset consecutive hits if we have not been hit recently
    if state["last_hit_ts"] > 0 and (now - state["last_hit_ts"]) > COOLDOWN_WINDOW:
        state["consecutive_hits"] = 0
    return state


def _detect_env_signals() -> bool:
    """Report the operator's manual rate-limit override.

    These two variables are set BY HAND, not by Claude Code. The original
    comment called `ANTHROPIC_RATE_LIMITED` "a hypothetical env var CC might
    set", and it stayed hypothetical: neither name appears in any Claude Code
    release note across the whole corpus, so nothing upstream will ever
    populate them. Reading them as a CC signal means this returns False
    forever and the backoff never engages on its own.

    What they are good for is a deliberate override — an operator who knows
    they are limited can export one and force backoff. `/api-status` surfaces
    both (skills/api-status/SKILL.md), which is the surface that makes them
    worth keeping.

    Automatic detection lives elsewhere and works: hooks/repl-429-autodetect-hook.py
    classifies the real thing from transcript text via lib/rate_limit_classifier.py.
    """
    if os.environ.get("ANTHROPIC_RATE_LIMITED", "").lower() in ("1", "true", "yes"):
        return True
    if os.environ.get("ANTHROPIC_RATE_LIMIT_RETRY_AFTER", ""):
        return True
    return False


def _compute_delay(consecutive_hits: int) -> float:
    """Compute exponential backoff delay with jitter.

    delay = BASE_DELAY * 2^(consecutive_hits - 1) + jitter
    Capped at MAX_DELAY.
    """
    if consecutive_hits <= 0:
        return 0.0
    exponent = min(consecutive_hits - 1, 10)  # cap exponent to prevent overflow
    raw_delay = BASE_DELAY * (2 ** exponent)
    capped = min(raw_delay, MAX_DELAY)
    jitter = capped * JITTER_FACTOR * (2 * random.random() - 1)
    return max(0.0, capped + jitter)


def record_rate_limit(now: float | None = None) -> dict:
    """Record a rate limit event.

    Call this when a rate limit error is detected (e.g. HTTP 429 or CC error message).
    Returns the updated state including the recommended delay.
    """
    if now is None:
        now = time.time()
    state = _load_state()
    state = _prune_old_events(state, now)
    state["events"].append(now)
    state["consecutive_hits"] += 1
    state["last_hit_ts"] = now
    _save_state(state)
    delay = _compute_delay(state["consecutive_hits"])
    return {
        "recorded": True,
        "consecutive_hits": state["consecutive_hits"],
        "delay_seconds": delay,
        "event_count_in_window": len(state["events"]),
    }


def check_rate_limit(now: float | None = None) -> dict:
    """Check whether we should throttle based on recent rate limit events.

    Returns a dict with:
        throttled (bool): whether the caller should wait
        delay_seconds (float): recommended wait time (0 if not throttled)
        consecutive_hits (int): number of consecutive rate limit events
        env_signal (bool): whether the operator has MANUALLY exported a
            rate-limit override. Claude Code never sets these variables,
            so this is False unless a human set it deliberately — it is
            not automatic detection. Automatic detection is
            hooks/repl-429-autodetect-hook.py.
    """
    if now is None:
        now = time.time()

    env_signal = _detect_env_signals()
    state = _load_state()
    state = _prune_old_events(state, now)

    # If env says we are rate limited, record it as a hit
    if env_signal:
        state["events"].append(now)
        state["consecutive_hits"] += 1
        state["last_hit_ts"] = now
        _save_state(state)

    hits = state["consecutive_hits"]
    delay = _compute_delay(hits)

    # Throttled if we have recent hits and have not waited long enough
    time_since_last = now - state["last_hit_ts"] if state["last_hit_ts"] > 0 else float("inf")
    throttled = hits > 0 and time_since_last < delay

    return {
        "throttled": throttled,
        "delay_seconds": delay if throttled else 0.0,
        "consecutive_hits": hits,
        "env_signal": env_signal,
        "events_in_window": len(state["events"]),
    }


def clear_rate_limit_state() -> dict:
    """Clear all rate limit state. Used after successful recovery."""
    if RATE_LIMIT_STATE.exists():
        RATE_LIMIT_STATE.unlink()
        return {"cleared": True}
    return {"cleared": False, "reason": "no state file"}


def get_backoff_delay(consecutive_hits: int) -> float:
    """Public interface to compute backoff delay for a given hit count.

    Useful for testing and for callers that manage their own state.
    """
    return _compute_delay(consecutive_hits)
