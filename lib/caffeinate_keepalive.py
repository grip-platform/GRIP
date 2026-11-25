"""Caffeinate keepalive — pure-function logic for the keepalive tick.

Origin: 2026-05-26. V>>'s ultrathink ask: "create a /loop to make sure
caffeinate re-enables before it expires."

`caffeinate -ims -t $max_seconds` is the GRIP wrapper invocation
(lib/intelligent-caffeinate.sh::caffeinate_start_for_rsi). max_seconds =
GRIP_CAFFEINATE_MAX_HOURS × 3600, default 8h × 3600 = 28800s. After the
`-t` window the process exits cleanly, the PID file goes stale, and the
machine can sleep mid-RSI / mid-autonomous-loop. This module gives the
keepalive tick a pure-function decision core: WHEN to restart, IF to be
active at all, and AGE arithmetic on `ps -o etime=` output.

Pure-function discipline (syscall-doctrine compliance): no subprocess,
no file I/O, no env reads. All state comes in as arguments; all decisions
come out as return values. The bash wrapper (`bin/caffeinate-keepalive`)
does the I/O — `ps`, `kill`, PID-file reads, env checks. Same shape as
`lib/agora_verdict_router.py` (PR #3250).

Decision semantics:

  should_activate(...) → bool
      "Should caffeinate be running right now?"
      True iff currently running OR any autonomous-mode signal is set
      (RSI lock, autonomous-state level > 0, manual keepalive marker).
      False otherwise — the keepalive does nothing if there is no work to
      protect (no point keeping the machine awake for nothing).

  should_restart(age_seconds, max_seconds, safety_margin_seconds=3600) → bool
      "Is the current caffeinate process close enough to its `-t` expiry
      that we should rotate it NOW, before it dies?"
      True iff (max_seconds - age_seconds) <= safety_margin_seconds.
      With 8h TTL + 1h margin: restart at age >= 7h. Tick should fire on
      a cadence < (TTL - margin) to never miss the window — 1h is the
      canonical cadence (7 ticks of grace before each rotation).

  parse_etime_to_seconds(etime_str) → int | None
      Parse `ps -o etime= -p PID` output ([[DD-]hh:]mm:ss) to integer
      seconds. Returns None on malformed input — caller (bash wrapper)
      treats None as "unknown age, restart conservatively".

Falsification: each function is wrong if (a) it ever recommends stopping
caffeinate while RSI lock is held (would let machine sleep during real
RSI work), (b) it ever fails to restart before the natural `-t` expiry
under default cadence + margin, or (c) parse_etime returns wrong seconds
for any valid `ps` etime format. Each tested in
tests/test_caffeinate_keepalive.py with real-shaped inputs.
"""

from __future__ import annotations

import re

# Default safety margin: rotate when ≤1h remains before natural expiry.
# Tick cadence must be ≤ (max_seconds - DEFAULT_SAFETY_MARGIN_S), i.e. ≤7h
# at the default 8h TTL. 1h cadence gives 7 ticks of grace per window.
DEFAULT_SAFETY_MARGIN_S = 3600

# Default max from GRIP_CAFFEINATE_MAX_HOURS default of 8h.
DEFAULT_MAX_S = 8 * 3600

# `ps -o etime=` format: [[DD-]hh:]mm:ss — and the BSD/macOS-edge seconds-only
# form (e.g. bare "42" right after process start on some kernel/ps builds).
# AGORA #3253 Speaker C concern: a bare-seconds etime fell through `_ETIME_RE`
# silently returning None and triggered an unwanted ROTATE. The seconds-only
# fallback below catches that without loosening mm:ss validation.
_ETIME_RE = re.compile(
    r"^\s*(?:(?P<days>\d+)-)?(?:(?P<hours>\d+):)?(?P<minutes>\d+):(?P<seconds>\d+)\s*$"
)


def should_activate(
    is_running: bool,
    rsi_lock_exists: bool,
    autonomous_level: int,
    manual_marker_exists: bool,
) -> bool:
    """Decide whether caffeinate should be active on this tick.

    True iff ANY of these hold:
      - caffeinate is already running (don't drop a session V>> started manually)
      - rsi-active.lock exists (RSI loop is running)
      - autonomous_level > 0 (autonomous mode marker)
      - manual_marker_exists (operator set state/caffeinate-keepalive.lock)

    The "already running" condition makes the keepalive transparent — if
    V>> runs /caffeinate manually for a long task that has no other
    autonomous signal, the keepalive still keeps it alive.

    Returns False when there is no work to protect — no point burning
    battery + display on with nothing to do.
    """
    if is_running:
        return True
    if rsi_lock_exists:
        return True
    if autonomous_level > 0:
        return True
    if manual_marker_exists:
        return True
    return False


def should_restart(
    age_seconds: int | None,
    max_seconds: int = DEFAULT_MAX_S,
    safety_margin_seconds: int = DEFAULT_SAFETY_MARGIN_S,
) -> bool:
    """Decide whether to rotate (stop + start) the running caffeinate process.

    True iff the running process is close enough to its `-t` expiry that
    we should rotate NOW. Specifically: remaining_seconds ≤ safety_margin.

    `age_seconds = None` is treated as "unknown age" → True (rotate
    conservatively; a process whose age we can't read is suspect anyway).

    `age_seconds < 0` or `max_seconds ≤ 0` → False (invalid input; do
    nothing rather than make a wrong call).
    """
    if age_seconds is None:
        return True
    if age_seconds < 0:
        return False
    if max_seconds <= 0:
        return False
    if safety_margin_seconds < 0:
        return False
    remaining = max_seconds - age_seconds
    return remaining <= safety_margin_seconds


_ETIME_SECONDS_ONLY_RE = re.compile(r"^\s*(?P<seconds>\d+)\s*$")


def parse_etime_to_seconds(etime_str: str | None) -> int | None:
    """Parse `ps -o etime= -p PID` output to integer seconds.

    Format: [[DD-]hh:]mm:ss (canonical). Examples:
        "12:34"        → 12 min 34 s         = 754
        "01:23:45"     → 1h 23m 45s          = 5025
        "2-03:45:00"   → 2d 3h 45m 0s        = 186300
        "42"           → 42 s                = 42  (BSD/macOS-edge seconds-only)
        ""             → None (no process)
        "garbage"      → None (malformed)

    AGORA #3253 fix: a bare-seconds form ("42") observed on some BSD/macOS ps
    builds was silently returning None → unwanted ROTATE. The seconds-only
    fallback catches that without loosening canonical mm:ss validation.
    """
    if etime_str is None or not etime_str.strip():
        return None
    m = _ETIME_RE.match(etime_str)
    if m:
        days = int(m.group("days") or 0)
        hours = int(m.group("hours") or 0)
        minutes = int(m.group("minutes"))
        seconds = int(m.group("seconds"))
        return days * 86400 + hours * 3600 + minutes * 60 + seconds
    m = _ETIME_SECONDS_ONLY_RE.match(etime_str)
    if m:
        return int(m.group("seconds"))
    return None


# Action labels — the bash wrapper switches on these.
ACTION_NOOP = "noop"           # nothing to protect; do nothing
ACTION_START = "start"         # not running but should be → start
ACTION_ROTATE = "rotate"       # running but near expiry → stop + start
ACTION_HEALTHY = "healthy"     # running with comfortable margin → log only (the wrapper still replaces an old-flags keep-awake)


def route_action(
    is_running: bool,
    rsi_lock_exists: bool,
    autonomous_level: int,
    manual_marker_exists: bool,
    age_seconds: int | None,
    max_seconds: int = DEFAULT_MAX_S,
    safety_margin_seconds: int = DEFAULT_SAFETY_MARGIN_S,
) -> str:
    """Combined router: should_activate × should_restart → single action label.

    Decision tree:
      ¬should_activate              → ACTION_NOOP
      should_activate ∧ ¬is_running → ACTION_START
      should_activate ∧ is_running ∧ should_restart → ACTION_ROTATE
      should_activate ∧ is_running ∧ ¬should_restart → ACTION_HEALTHY
    """
    if not should_activate(is_running, rsi_lock_exists, autonomous_level, manual_marker_exists):
        return ACTION_NOOP
    if not is_running:
        return ACTION_START
    if should_restart(age_seconds, max_seconds, safety_margin_seconds):
        return ACTION_ROTATE
    return ACTION_HEALTHY
