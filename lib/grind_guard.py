"""Grind-guard: a scope-lock detector for the low-value infrastructure grind.

Built to the four recalibration axes an exogenous council prescribed for GRIP's
self-report Stop-hook (verdict RECALIBRATE, seal c586d543, 2026-08-18), so it is
NOT a naive per-turn nag:

  1. DECISION-GATING  — fires ONCE when the next action would be low-value
     tail-work, never on healthy iteration; resets on a ship (commit/PR).
  2. WHOLE-SESSION    — scores the session arc, not a local burst: the core must
     already have shipped, and the remaining edits must be HETEROGENEOUS (many
     distinct files = a cosmetic tail) rather than one root cause (deep work).
  3. SUPPRESS HEALTHY — silent while the core is still being delivered
     (not shipped), and silent during single-root-cause deep work.
  4. MEASURABLE       — every fire is logged with an outcome-label slot so the
     benefit is falsifiable, not asserted. Falsifier: retire the guard if fewer
     than 2 of the next 20 fires precede a correct ship-and-hand-back decision.

This module is PURE (no I/O). hooks/grind-guard.py owns the state file, the log,
and the systemMessage emission. Sibling doctrine:
memory/feedback_stop_the_low_value_infra_grind.md.
"""
from __future__ import annotations

import re

# Tunables. Kept here so the hook, the tests, and any future /evo calibration
# read one source of truth.
THRESHOLD = 8          # consecutive low-value actions since the last ship
HETEROGENEITY = 3      # distinct edited files in the streak => tail, not deep work

_TEST_PATH = re.compile(r"(^|/)(tests?/|test_[^/]*\.py$|[^/]*_test\.py$)")
_PYTEST = re.compile(r"\bpytest\b|python.*-m\s+pytest|\bvitest\b|\bgo\s+test\b")
_SHIP = re.compile(r"\bgit\s+commit\b|\bgh\s+pr\s+(create|merge)\b|\bgit\s+push\b")


def fresh_state() -> dict:
    """The zero state for a session."""
    return {"streak": 0, "targets": [], "shipped": False, "fired_at": 0}


def classify_action(tool_name: str, tool_input: dict, tool_response: str = "") -> str:
    """One tool call -> 'ship' | 'low_value' | 'neutral'.

    - ship:      a commit / PR-create / PR-merge / push (the core reaching origin)
    - low_value: an Edit/Write to a test file, or a Bash pytest/vitest run
    - neutral:   everything else (reads, production-code edits, other bash) — these
                 neither advance nor reset the grind streak.
    """
    ti = tool_input or {}
    if tool_name == "Bash":
        cmd = str(ti.get("command", ""))
        if _SHIP.search(cmd):
            return "ship"
        if _PYTEST.search(cmd):
            return "low_value"
        return "neutral"
    if tool_name in ("Edit", "Write", "MultiEdit"):
        path = str(ti.get("file_path", "") or ti.get("path", ""))
        if _TEST_PATH.search(path):
            return "low_value"
        return "neutral"
    return "neutral"


def update(state: dict, tool_name: str, tool_input: dict, tool_response: str = "",
           *, threshold: int = THRESHOLD, heterogeneity: int = HETEROGENEITY):
    """Fold one tool call into the session state.

    Returns (new_state, fire). ``fire`` is None (stay quiet) or a dict
    {message, streak, distinct_targets} when the guard should advise. Fail-safe by
    construction: the caller treats any exception as "no fire".
    """
    s = {
        "streak": int(state.get("streak", 0)),
        "targets": list(state.get("targets", [])),
        "shipped": bool(state.get("shipped", False)),
        "fired_at": int(state.get("fired_at", 0)),
    }
    kind = classify_action(tool_name, tool_input, tool_response)

    if kind == "ship":
        # A ship resets the streak and re-arms the guard. shipped stays True so
        # that low-value work AFTER the first ship is what counts as a tail.
        return {"streak": 0, "targets": [], "shipped": True, "fired_at": 0}, None

    if kind == "neutral":
        return s, None

    # kind == "low_value": advance the streak, remember the distinct target.
    s["streak"] += 1
    path = str((tool_input or {}).get("file_path", "") or (tool_input or {}).get("path", ""))
    if path and path not in s["targets"]:
        s["targets"].append(path)

    distinct = len(s["targets"])
    # Axis 1 (decision-gating) + Axis 2/3 (whole-session, suppress deep work):
    # fire only when the core has shipped, the streak has crossed a band, and the
    # edits are heterogeneous. Single-fire per band via fired_at.
    should_fire = (
        s["shipped"]
        and s["streak"] >= threshold
        and distinct >= heterogeneity
        and (s["fired_at"] == 0 or s["streak"] >= s["fired_at"] + threshold)
    )
    if not should_fire:
        return s, None

    s["fired_at"] = s["streak"]
    msg = (
        f"[GRIND-GUARD] {s['streak']} low-value test-edits across {distinct} files "
        "since the last ship, and the core already shipped. This has the shape of a "
        "cosmetic tail, not deep work on one cause. Consider shipping the current "
        "green and handing back the residual with an itemized report "
        "(memory/feedback_stop_the_low_value_infra_grind.md), rather than "
        "force-greening every last test."
    )
    return s, {"message": msg, "streak": s["streak"], "distinct_targets": distinct}
