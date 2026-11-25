#!/usr/bin/env python3
"""
auto_converge.py — Auto-converging RL system for GRIP (Issue #214).

Wires the `converge()` kernel into the RSI orchestration loop, creating
a criterion-driven recursive self-improvement cycle.

Phase 1: converge()-driven RSI
  criterion: test_pass_rate >= 95% AND reviews >= 85% (the share of merged pull
             requests AGORA passed at its first sitting, held in ralph_avg; GRIP#9668)
             Meta-work is out of the criterion until something records it.
  improve: execute next highest-priority candidate from queue
  max_depth: 5 per cycle, 2 cycles/24h cap

Phase 2 (future): RLHF
  Human accept/reject signals become gradient for candidate generation.

Usage:
    python3 lib/auto_converge.py status    # Show convergence state
    python3 lib/auto_converge.py check     # Check if criterion is met
    python3 lib/auto_converge.py run       # Run one convergence cycle (dry-run)

Connects to:
    lib/converge.py          — recursive convergence kernel
    lib/rsi_orchestrator.py  — candidate queue, budget, metrics
    lib/metrics_tracker.py   — meta_work_ratio signal
    lib/cube.py              — CUBE convergence tracking
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

from lib.converge import converge, ConvergenceResult
from lib.converge_combinators import VelocityTracker, composite_scorer
from claude_dir import get_claude_dir  # noqa: E402

# Resolves $CLAUDE_DIR when set, else $HOME/.claude (rules/path-portability.md).
# Not merely a style fix: the test-pass-rate cache below lives under this root,
# so the previous hardcoded `Path.home() / ".claude"` made a run inside a
# worktree read and write the SHARED tree's cache rather than its own. A shared
# tree is a valid checkout answering correct questions about the wrong ref, so
# nothing announces that mistake (rules/shared-tree-is-not-canonical.md).
CLAUDE_DIR = get_claude_dir()
STATE_FILE = CLAUDE_DIR / "state" / "rsi" / "auto-converge-state.json"
DELIGHT_SNAPSHOTS_FILE = CLAUDE_DIR / "learning" / "delight_snapshots.jsonl"
GENOME_FILE = CLAUDE_DIR / "cache" / "genome.json"
MAX_CYCLES_PER_DAY = 2
MAX_DEPTH_PER_CYCLE = 5


# =========================================================================
# RSI State — the "state" that flows through converge()
# =========================================================================

@dataclass
class RSIState:
    """State object for the auto-converging RSI loop.

    This is the 'state' parameter in converge(state, criterion, improve).
    Each field represents a measurable signal from the GRIP system.
    """
    test_pass_rate: float = 0.0        # 0.0-1.0 — only meaningful if measured
    test_rate_measured: bool = True    # False => the 0.0 above is "unknown"
    review_measured: bool = True       # False => ralph_avg 0.0 is "no reading"
    meta_work_measured: bool = True    # False => 0.0 is "no work recorded"; shown
                                       # only, never graded (GRIP#9668)
    ralph_avg: float = 0.0             # 0-100: % of merged pull requests AGORA
                                       # passed at the first sitting (GRIP#9668);
                                       # the field keeps the retired Ralph
                                       # reviewer's name
    meta_work_pct: float = 0.0         # 0.0-1.0
    candidates_completed: int = 0
    candidates_remaining: int = 0
    cycle_depth: int = 0
    stall_detected: bool = False
    timestamp: str = ""

    @property
    def score(self) -> float:
        """Composite convergence score (0-100).

        The test pass rate and the review rate, weighted 40 to 35 as before
        and scaled to 100. Meta-work's 25 points left the composite when it
        left the criterion (GRIP#9668): with nothing recording meta-work, an
        empty reading earned all 25.
        """
        test_component = self.test_pass_rate * 40
        review_component = (self.ralph_avg / 100) * 35
        return (test_component + review_component) * 100 / 75


# =========================================================================
# Criterion — when to stop improving
# =========================================================================

def unmeasured_inputs(state: RSIState) -> list[str]:
    """Name every criterion input whose value is ABSENT rather than measured.

    Each criterion input defaults to 0.0, and 0.0 is a legitimate reading for
    every one of them. The paired `*_measured` flags are the only thing telling
    "we looked and saw zero" apart from "we never got a reading". Any caller
    that reads the number without consulting this list is grading absent
    evidence as if it were evidence.

    Returned as names rather than a bare bool so both the criterion and the
    status display can say WHICH input is missing — a caller that only learns
    "something is unmeasured" cannot tell the operator what to go fix.
    """
    return [
        name
        for name, measured in (
            ("tests", state.test_rate_measured),
            ("reviews", state.review_measured),
        )
        if not measured
    ]


def rsi_criterion(state: RSIState) -> bool:
    """Auto-converging criterion for GRIP RSI.

    Satisfied when ALL conditions met:
    1. Every input actually measured (see the fail-closed note below)
    2. test_pass_rate >= 95%
    3. ralph_avg >= 85: of the pull requests merged in 7 days, 85% passed AGORA
       at their first sitting. Meta-work left the criterion (GRIP#9668): nothing
       records it, so it was unmeasured on every run.
    4. No stall detected (scoring attractor broken)

    This maps to the converge() pattern:
    - criterion(state) returns True → stop improving
    - criterion(state) returns False → call improve(state) again

    Unmeasured inputs fail CLOSED — deliberately, and said here rather than
    reached by accident through a 0.0. Returning True authorises the RSI loop
    to stop improving and declare success on work it merges itself, so a bar
    that was never evaluated must never read as a bar that was met.

    The alternative — dropping an unmeasured input out of the conjunction —
    was rejected because it degenerates exactly when it matters most: with
    every input unmeasured the conjunction is empty, and an empty conjunction
    is vacuously True, so the loop would declare convergence on no evidence at
    all. It is unsound per-input too, because nothing here can distinguish
    "this repo has no test suite" from "pytest timed out this run" — both
    arrive as None — so dropping the clause would let a transient timeout
    silently lower the standard.

    Failing closed does not hang the loop: MAX_DEPTH_PER_CYCLE bounds the
    cycle regardless, so the honest outcome of an unmeasurable run is a cycle
    that ends without declaring success, not one that never ends.
    """
    if state.stall_detected:
        return False  # Force continue — stall means we're NOT converged

    if unmeasured_inputs(state):
        # Absent evidence, not failing evidence. Distinguishable from a genuine
        # measured 0.0 via unmeasured_inputs(), which names what was missing.
        return False

    return state.test_pass_rate >= 0.95 and state.ralph_avg >= 85.0


# =========================================================================
# Scorer — track best state across iterations
# =========================================================================

def rsi_scorer(state: RSIState) -> float:
    """Score function for converge()'s best-state tracking."""
    return state.score


# =========================================================================
# Measure — collect current signals from the system
# =========================================================================

# The cache has always written a `timestamp`, and the reader never read it, so
# any value once written was trusted forever: a single stale write poisoned the
# convergence criterion permanently, with no expiry and nothing to announce it.
#
# 24h is GRIP's existing RSI cadence unit — MAX_CYCLES_PER_DAY is 2 and the RSI
# scan-window floor is 24 hours — so a rate measured within the last day is
# same-day evidence about the same tree. Shorter is not free: re-measuring costs
# a ~19-minute suite run and quick mode cannot re-measure at all, so a tight TTL
# would simply strand `check --quick` at UNMEASURED for most of the day.
_TEST_RATE_TTL_SEC = 24 * 60 * 60

# A timestamp AHEAD of now is a broken clock or a corrupt write, not freshness —
# accepting it would reinstate the indefinite trust this TTL exists to remove.
# The small grace absorbs ordinary skew (a cache root shared across machines)
# without accepting a stamp that is meaningfully in the future.
_CLOCK_SKEW_GRACE_SEC = 300


def _cache_entry_is_fresh(stamp: object, now: datetime | None = None) -> bool:
    """True only for a parseable timestamp whose age is within the TTL.

    Fails toward re-measuring in every ambiguous case: a missing, non-string,
    unparseable, or future-dated stamp is stale. Treating an unknown age as
    fresh is exactly the behaviour being fixed, so it is never the default here.
    """
    if not isinstance(stamp, str):
        return False
    try:
        written = datetime.fromisoformat(stamp)
    except ValueError:
        return False
    if written.tzinfo is None:
        written = written.replace(tzinfo=timezone.utc)
    age_sec = ((now or datetime.now(timezone.utc)) - written).total_seconds()
    return -_CLOCK_SKEW_GRACE_SEC <= age_sec <= _TEST_RATE_TTL_SEC


def _read_cached_test_rate() -> float | None:
    """Read the last-known test pass rate, or None when there is no usable one.

    None rather than 0.0 on a miss. A missing or corrupt cache means "no
    measurement", and returning 0.0 for it fabricates a perfect-failure reading
    that is indistinguishable from a suite where nothing passed. Callers that
    render that as a grade make the convergence criterion unsatisfiable.

    An entry older than the TTL is also a miss. It was written under a tree that
    no longer exists, and reporting it as the current rate is a confident answer
    about the wrong code.

    Still never raises — an unreadable cache is a normal condition, not an error.
    """
    cache_file = CLAUDE_DIR / "cache" / "test-pass-rate.json"
    try:
        if not cache_file.exists():
            return None
        data = json.loads(cache_file.read_text())
        if not _cache_entry_is_fresh(data.get("timestamp")):
            logger.warning(
                "cached test pass rate is stale or undated (TTL %ds) — test rate "
                "unmeasured; run `check --full` to re-measure", _TEST_RATE_TTL_SEC,
            )
            return None
        return float(data["pass_rate"])
    except (json.JSONDecodeError, OSError, ValueError, KeyError,
            TypeError, AttributeError):
        pass
    return None


def _resolve_meta_work(days: int = 7) -> tuple[float, bool]:
    """Return (meta_work_pct, measured) over the window.

    Shown by `check` only: meta-work left the criterion and the composite
    (GRIP#9668) because nothing records the session categories it counts.
    While it was graded, an empty dataset reported meta_pct 0.0 and earned the
    composite's full 25 meta-work points, which is why `total` is the
    discriminator: zero recorded items means no measurement, not zero percent
    meta-work.
    """
    try:
        from lib.metrics_tracker import get_meta_work_ratio
        ratio = get_meta_work_ratio(days=days)
    except (ImportError, OSError, ValueError, AttributeError, TypeError) as exc:
        logger.warning("meta-work metrics unavailable (%s) — unmeasured", exc)
        return 0.0, False
    if not isinstance(ratio, dict) or not ratio.get("total"):
        return 0.0, False
    return ratio.get("meta_pct", 0.0), True


def _resolve_test_pass_rate(quick: bool) -> float | None:
    """Best available pass rate, or None if it genuinely could not be measured.

    Quick mode consults only the cache. A full run falls back to the cache when
    the run itself yields no measurement, so one transient timeout does not
    discard a known-good earlier reading.
    """
    if quick:
        return _read_cached_test_rate()
    rate = _run_pytest_pass_rate()
    return rate if rate is not None else _read_cached_test_rate()


def _write_cached_test_rate(rate: float) -> None:
    """Write test pass rate to cache for quick lookups."""
    cache_file = CLAUDE_DIR / "cache" / "test-pass-rate.json"
    try:
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps({
            "pass_rate": round(rate, 4),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }))
    except OSError:
        pass


_REVIEW_CACHE = "review-pass-rate.json"


def _read_review_record() -> dict | None:
    """The newest full check's review record, or None when there is none or it
    is older than the test rate's 24-hour TTL."""
    try:
        data = json.loads((CLAUDE_DIR / "cache" / _REVIEW_CACHE).read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not _cache_entry_is_fresh(data.get("timestamp")):
        return None
    return data


def _read_cached_review_rate() -> float | None:
    """The rate the newest full check recorded, or None when it recorded none."""
    rate = (_read_review_record() or {}).get("pass_rate")
    return float(rate) if isinstance(rate, (int, float)) else None


def _cached_review_reason() -> str:
    """Why the newest full check recorded no rate, or "" (GRIP#9886)."""
    return str((_read_review_record() or {}).get("reason") or "")


def _write_cached_review_rate(rate: float | None, sittings: int, reason: str = "") -> None:
    """Record the newest full check's reading. None records "unmeasured", so a
    later quick check never shows an older rate in its place."""
    cache_file = CLAUDE_DIR / "cache" / _REVIEW_CACHE
    try:
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps({
            "pass_rate": None if rate is None else round(rate, 4),
            "sittings": sittings,
            "reason": reason,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }))
    except OSError:
        pass


def _resolve_reviews(quick: bool) -> tuple[float, bool]:
    """(review rate 0-100, measured) for the criterion (GRIP#9668).

    The rate is the share of pull requests merged in 7 days that AGORA passed
    at the first sitting (lib/review_pass_rate.py); fewer than MIN_SITTINGS
    (5) first sittings is unmeasured. Measuring takes about half a minute of
    GitHub queries, so a full check measures and a quick check reads what the
    newest full check recorded. A full check reports only what it measured: a
    failed or too-small measurement is unmeasured, never replaced by an older
    cached rate (AGORA on GRIP#9882).
    """
    if quick:
        rate = _read_cached_review_rate()
    else:
        from lib import review_pass_rate
        rate, sittings, reason = review_pass_rate.measure(CLAUDE_DIR)
        if rate is None:
            logger.warning("review rate unmeasured: %s", reason)
        _write_cached_review_rate(rate, sittings, reason)
    return (rate * 100, True) if rate is not None else (0.0, False)


# The line `pytest -q` prints last, such as "2 failed, 98 passed in 3.21s".
_SUMMARY_LINE = re.compile(r"\bin \d+(?:\.\d+)?s\b")
# How much of that line a warning quotes, so a stray long line cannot flood
# the log (AGORA on GRIP#9590).
_SUMMARY_SHOWN = 200

# Only pytest's exit codes 0 (every test passed) and 1 (some failed) are a
# measurement. Any other code is not, a missing one included. The codes pytest
# documents for a session it stopped early are named below (one collection
# error interrupts the whole session, so no test runs), and a negative code is
# a signal. An allowlist, so the sensor never relies on a killed run printing
# no summary (AGORA on GRIP#9590).
_PYTEST_MEASURED_EXITS = frozenset({0, 1})
_PYTEST_STOPPED_EARLY = {2: "interrupted", 3: "internal error", 4: "usage error",
                         5: "no tests collected"}


def _is_signal(code: object) -> bool:
    """A negative exit code means the process died from a signal."""
    return isinstance(code, int) and code < 0


def _summary_line(output: str) -> str:
    """The last line of pytest output that reads like its final summary, or ""."""
    return next((line for line in reversed(output.splitlines())
                 if _SUMMARY_LINE.search(line)), "")


def _parse_pytest_counts(output: str) -> tuple[int, int, int]:
    """Extract (passed, failed, errors) from pytest's final summary line.

    Only that line counts: a failure message printed above it, such as
    "AssertionError: 7 passed", is test output, not a count. Returns zeros
    when the summary is absent — the caller MUST treat an all-zero total as
    "could not measure", never as "nothing passed".
    """
    summary = _summary_line(output)

    def _count(pattern: str) -> int:
        match = re.search(pattern, summary)
        return int(match.group(1)) if match else 0

    return _count(r"(\d+) passed"), _count(r"(\d+) failed"), _count(r"(\d+) error")


# Measured 2026-08-14 on the development machine with /usr/bin/time -p: 1115.69s
# wall clock for the full suite, 36,526 tests collected, 21.74s of that spent in
# collection alone. The prior 900s default sat BELOW the measured runtime, so
# `check --full` was SIGKILLed on every single run and returned None every time —
# the identical failure to the 120s budget it replaced, one measurement behind
# reality. To its credit the caller then refuses to cache a value it never got,
# so the fault stayed loud instead of poisoning the cache; but the mode was dead.
#
# 1800s is 1.61x the measured time rather than a figure pinned to it, because the
# two failure directions are not symmetric. Too tight kills the feature outright.
# Too loose only spends wall clock on a run that run_with_kill terminates anyway.
# A CI runner or an older machine being half again as slow as this one is
# ordinary, and the checker must not break again the first time that happens.
# Still env-overridable, unchanged.
_PYTEST_BUDGET_SEC = int(os.environ.get("GRIP_RSI_PYTEST_TIMEOUT", "1800"))


def _unmeasured_reason(result: dict, timeout_sec: int) -> str | None:
    """Why a finished pytest run is no measurement, or None when it is one."""
    if result.get("killed"):
        return (f"pytest killed after {timeout_sec}s before reporting (raise "
                "GRIP_RSI_PYTEST_TIMEOUT if the suite legitimately needs longer)")
    code = result.get("returncode")
    if code in _PYTEST_MEASURED_EXITS:
        return None
    name = _PYTEST_STOPPED_EARLY.get(code, "killed by a signal" if _is_signal(code) else "unknown exit")
    summary = (_summary_line(result.get("output", "")) or "no summary line")[:_SUMMARY_SHOWN]
    return f"pytest stopped early ({name}, exit {code}: {summary})"


def _run_pytest_pass_rate(timeout_sec: int = _PYTEST_BUDGET_SEC) -> float | None:
    """Run pytest and return the pass rate, or None if it could not be measured.

    None is NOT zero. A killed run, a missing summary line, or a failure to
    launch all mean "no measurement" — and a caller that renders those as 0.0
    turns an unmeasurable sensor into a hard failing grade. That is precisely
    what happened: the criterion test_pass_rate >= 95% became unsatisfiable by
    construction, because 0.0 was the only value the sensor could ever emit.

    A None result never writes the cache. Caching a parse-failure zero is what
    let the fault survive across runs — every later quick lookup read that zero
    back as though it had been measured.

    Only pytest's exit codes 0 and 1 are measurements. A session it stopped
    early is not: one collection error interrupts the whole run, and its
    summary, such as "1 error in 2.0s", used to read as a pass rate of 0.0,
    cached for a day.
    """
    try:
        from lib.subprocess_utils import run_with_kill
        result = run_with_kill(
            [sys.executable, "-m", "pytest", str(_ROOT / "tests"), "-q", "--tb=no"],
            timeout=timeout_sec, kill_grace=5, cwd=str(_ROOT),
        )
    except (FileNotFoundError, OSError) as exc:
        logger.warning("pytest could not be launched (%s) — test rate unmeasured", exc)
        return None

    reason = _unmeasured_reason(result, timeout_sec)
    if reason:
        logger.warning("%s — test rate unmeasured", reason)
        return None

    passed, failed, errors = _parse_pytest_counts(result.get("output", ""))
    total = passed + failed + errors
    if total == 0:
        logger.warning("pytest produced no summary line — test rate unmeasured")
        return None

    rate = passed / total
    _write_cached_test_rate(rate)
    return rate


def measure_current_state(quick: bool = False) -> RSIState:
    """Collect current GRIP system signals into an RSIState.

    Args:
        quick: If True, skip expensive operations (pytest run, GitHub query)
               and use cached values instead. Used by the 'check' command for
               fast status without blocking on subprocess calls.

    Reads from:
    - pytest results (test_pass_rate) — skipped in quick mode, uses cache
    - AGORA first-sitting rate (ralph_avg, GRIP#9668) — cached in quick mode
    - metrics_tracker meta_work_ratio (meta_work_pct; shown, not graded)
    - rsi_orchestrator queue and budget (candidates, budget)
    """
    state = RSIState(timestamp=datetime.now(timezone.utc).isoformat())

    # 1. Test pass rate — None means unmeasured, which is NOT a 0% grade.
    measured = _resolve_test_pass_rate(quick)
    state.test_rate_measured = measured is not None
    state.test_pass_rate = measured if measured is not None else 0.0

    # 2. Reviews: AGORA's first-sitting rate (GRIP#9668). stall_detected is left to
    # the live velocity brake; the retired Ralph record's flag is frozen (GRIP#9882).
    state.ralph_avg, state.review_measured = _resolve_reviews(quick)

    # 3. Meta-work ratio -- shown by check, not graded (GRIP#9668); empty is unmeasured.
    state.meta_work_pct, state.meta_work_measured = _resolve_meta_work(days=7)

    # 4. Candidate queue
    try:
        from lib.rsi_orchestrator import get_pending_candidates
        candidates = get_pending_candidates()
        state.candidates_remaining = len(candidates)
    except (ImportError, Exception):
        pass

    return state


# =========================================================================
# Improve — execute the next improvement step
# =========================================================================

def rsi_improve(state: RSIState) -> RSIState:
    """Execute one improvement step and return updated state.

    This is the 'improve' callback for converge().
    In Phase 1, it selects and "executes" the next candidate.
    In dry-run mode, it simulates execution.

    The actual improvement (implementing code changes, running tests,
    creating PRs) is done by the RSI orchestrator externally.
    This function captures the intent and returns the projected state.
    """
    new_state = measure_current_state()
    new_state.cycle_depth = state.cycle_depth + 1
    new_state.candidates_completed = state.candidates_completed + 1

    # Record the improvement step
    _record_step(state, new_state)

    return new_state


def _record_step(before: RSIState, after: RSIState) -> None:
    """Record a convergence step to the state file."""
    try:
        from lib.rsi_orchestrator import record_metric
        record_metric("auto_converge_step", {
            "depth": after.cycle_depth,
            "before_score": before.score,
            "after_score": after.score,
            "criterion_met": rsi_criterion(after),
            "test_pass_rate": after.test_pass_rate,
            "ralph_avg": after.ralph_avg,
            "meta_work_pct": after.meta_work_pct,
        })
    except (ImportError, Exception):
        pass


# =========================================================================
# Cycle Management — rate limiting and persistence
# =========================================================================

@dataclass
class VelocitySnapshot:
    """Point-in-time velocity measurement."""
    timestamp: str = ""
    velocity: float = 0.0
    acceleration: float = 0.0
    is_stalling: bool = False
    score: float = 0.0
    depth: int = 0


@dataclass
class CycleState:
    """Tracks cycle count for rate limiting (2 cycles/24h cap)."""
    cycles_today: int = 0
    last_cycle_date: str = ""
    total_cycles: int = 0
    best_score: float = 0.0
    history: list = field(default_factory=list)
    velocity_history: list = field(default_factory=list)


def load_cycle_state() -> CycleState:
    """Load cycle state from persistent storage."""
    if not STATE_FILE.exists():
        return CycleState()
    try:
        data = json.loads(STATE_FILE.read_text())
        return CycleState(**{k: v for k, v in data.items() if k in CycleState.__dataclass_fields__})
    except (json.JSONDecodeError, OSError, TypeError):
        return CycleState()


def save_cycle_state(state: CycleState) -> None:
    """Save cycle state to persistent storage."""
    STATE_FILE.write_text(json.dumps(asdict(state), indent=2))


def can_run_cycle() -> tuple[bool, str]:
    """Check if a new convergence cycle is allowed.

    Checks: daily rate limit, weekly budget conservation, session gating.
    """
    cs = load_cycle_state()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    if cs.last_cycle_date != today:
        # Reset daily counter
        cs.cycles_today = 0
        cs.last_cycle_date = today
        save_cycle_state(cs)

    if cs.cycles_today >= MAX_CYCLES_PER_DAY:
        return False, f"Rate limit: {cs.cycles_today}/{MAX_CYCLES_PER_DAY} cycles today"

    return True, f"OK: {cs.cycles_today}/{MAX_CYCLES_PER_DAY} cycles used today"


# =========================================================================
# Run — execute one convergence cycle
# =========================================================================

def run_cycle(dry_run: bool = True) -> ConvergenceResult:
    """Run one auto-converging RSI cycle.

    Uses converge() to recursively improve GRIP until criterion is met
    or max_depth is reached.

    Args:
        dry_run: If True, measure state but don't execute improvements.
    """
    allowed, msg = can_run_cycle()
    if not allowed:
        print(f"[AUTO-CONVERGE] Blocked: {msg}")
        initial = measure_current_state()
        return ConvergenceResult(
            best_state=initial,
            final_state=initial,
            satisfied=False,
            iterations=0,
            depth_reached=0,
            history=[],
            elapsed_sec=0.0,
            label="auto-converge-blocked",
        )

    print(f"[AUTO-CONVERGE] Starting cycle (dry_run={dry_run})")
    initial = measure_current_state()
    print(f"[AUTO-CONVERGE] Initial state: score={initial.score:.1f}, "
          f"tests={initial.test_pass_rate:.1%}, ralph={initial.ralph_avg:.0f}, "
          f"meta={initial.meta_work_pct:.1%}")

    # Wire VelocityTracker for stall detection and strategy switching
    velocity_scorer = lambda s: s.score if isinstance(s, RSIState) else 0.0
    tracker = VelocityTracker(velocity_scorer)

    base_improve = rsi_improve if not dry_run else lambda s: measure_current_state()
    improve_fn = tracker.as_improve_wrapper(
        base_improve,
        stall_strategy=_stall_recovery_improve,
    )

    result = converge(
        state=initial,
        criterion=rsi_criterion,
        improve=improve_fn,
        max_depth=MAX_DEPTH_PER_CYCLE,
        _scorer=rsi_scorer,
        label="auto-converge-rsi",
    )

    # Capture velocity data
    velocity_snap = VelocitySnapshot(
        timestamp=datetime.now(timezone.utc).isoformat(),
        velocity=tracker.velocity,
        acceleration=tracker.acceleration,
        is_stalling=tracker.is_stalling,
        score=result.best_state.score if isinstance(result.best_state, RSIState) else 0.0,
        depth=result.depth_reached,
    )

    # Alert on stall via Slack (non-blocking)
    if tracker.is_stalling:
        _notify_stall(tracker, result)

    # Store velocity in KONO (non-blocking)
    _store_velocity_kono(velocity_snap)

    # Update cycle tracking
    cs = load_cycle_state()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if cs.last_cycle_date != today:
        cs.cycles_today = 0
        cs.last_cycle_date = today
    cs.cycles_today += 1
    cs.total_cycles += 1
    cs.best_score = max(cs.best_score, result.best_state.score)
    cs.history.append({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "depth": result.depth_reached,
        "satisfied": result.satisfied,
        "score": result.best_state.score,
        "elapsed_sec": result.elapsed_sec,
    })
    cs.velocity_history.append(asdict(velocity_snap))
    # Keep last 20 entries each
    cs.history = cs.history[-20:]
    cs.velocity_history = cs.velocity_history[-50:]
    save_cycle_state(cs)

    print(f"[AUTO-CONVERGE] Result: satisfied={result.satisfied}, "
          f"depth={result.depth_reached}/{MAX_DEPTH_PER_CYCLE}, "
          f"best_score={result.best_state.score:.1f}, "
          f"elapsed={result.elapsed_sec:.1f}s")

    return result


# =========================================================================
# Stall Recovery — alternative strategy when velocity drops
# =========================================================================

def _stall_recovery_improve(state: RSIState) -> RSIState:
    """Alternative improve strategy when convergence stalls.

    Instead of executing the next candidate linearly, this switches
    to a different approach: re-measuring with fresh data and marking
    stall_detected so the criterion forces another iteration.
    """
    new_state = measure_current_state()
    new_state.cycle_depth = state.cycle_depth + 1
    new_state.candidates_completed = state.candidates_completed
    new_state.stall_detected = True
    return new_state


def _notify_stall(tracker: VelocityTracker, result: ConvergenceResult) -> None:
    """Send Slack alert when convergence stalls. Non-blocking, best-effort."""
    try:
        msg = (
            f"[AUTO-CONVERGE STALL] velocity={tracker.velocity:.2f}, "
            f"accel={tracker.acceleration:.2f}, "
            f"depth={result.depth_reached}/{MAX_DEPTH_PER_CYCLE}, "
            f"score={result.best_state.score:.1f}"
        )
        # Write to a stall log for pair-mode pickup
        stall_log = CLAUDE_DIR / "cache" / "convergence-stall.log"
        stall_log.parent.mkdir(exist_ok=True)
        with open(stall_log, "a") as f:
            f.write(f"{datetime.now(timezone.utc).isoformat()} {msg}\n")

        # Send Slack notification (#210 integration)
        try:
            from lib.slack_notify import notify_error
            notify_error(
                error=msg,
                phase="auto-converge",
            )
        except (ImportError, Exception):
            pass  # Slack notification optional
    except Exception as _e:
        logger.debug("Stall detection write failed: %s", _e)


def _store_velocity_kono(snap: VelocitySnapshot) -> None:
    """Store velocity snapshot in KONO for cross-session trend analysis."""
    try:
        from lib.kono_substrate import MemorySubstrate
        kono = MemorySubstrate()
        content = (
            f"convergence velocity={snap.velocity:.3f} "
            f"accel={snap.acceleration:.3f} "
            f"stalling={snap.is_stalling} "
            f"score={snap.score:.1f} "
            f"depth={snap.depth}"
        )
        kono.store(
            domain="convergence-velocity",
            key=f"snap-{snap.timestamp[:10]}",
            content=content,
        )
    except (ImportError, Exception):
        pass  # KONO optional — don't break convergence cycle


# =========================================================================
# Delight Snapshot — composite user + AI delight metric (#155)
# =========================================================================

@dataclass
class DelightSnapshot:
    """Point-in-time delight measurement.

    Delight = (user_delight + ai_delight) / 2

    User delight signals (observable, no survey needed):
    - flow_state: consecutive operations without gate blocks
    - velocity: issues completed per hour
    - zero_interruption_streak: operations between gate denials
    - completion_ratio: issues completed / issues attempted

    AI delight signals (system-measurable):
    - genome_fitness_trend: improving = healthy evolution
    - converge_velocity: positive = productive iteration
    - pattern_discovery_count: novel insights found
    - kono_diversity: embedding dimension spread
    """
    timestamp: str = ""
    # User signals
    flow_state: float = 0.0           # 0-1 (fraction of ops without blocks)
    velocity: float = 0.0             # issues/hour
    zero_interruption_streak: int = 0
    completion_ratio: float = 0.0     # completed / attempted
    # AI signals
    genome_fitness_trend: float = 0.0  # delta from last generation
    converge_velocity: float = 0.0     # from VelocityTracker
    pattern_discovery_count: int = 0
    kono_diversity: float = 0.0        # 0-1 dimension spread
    # Composites
    user_delight: float = 0.0
    system_health: float = 0.0   # was ai_delight — not delight, just health indicators
    composite: float = 0.0

    def compute(self) -> None:
        """Compute composite from user delight + system health signals."""
        self.user_delight = (
            self.flow_state * 0.3
            + min(self.velocity / 3.0, 1.0) * 0.3  # cap at 3 issues/hr
            + min(self.zero_interruption_streak / 20, 1.0) * 0.2
            + self.completion_ratio * 0.2
        )
        self.system_health = (
            max(0, min(self.genome_fitness_trend + 0.5, 1.0)) * 0.3
            + max(0, min(self.converge_velocity, 1.0)) * 0.3
            + min(self.pattern_discovery_count / 5, 1.0) * 0.2
            + self.kono_diversity * 0.2
        )
        self.composite = (self.user_delight + self.system_health) / 2


def _genome_fitness_trend() -> float:
    """Compute genome fitness trend from cache/genome.json.

    Returns delta between last two fitness_history entries.
    Positive = improving. Zero if insufficient data.
    """
    try:
        data = json.loads(GENOME_FILE.read_text())
        history = data.get("fitness_history", [])
        if len(history) >= 2:
            return history[-1] - history[-2]
        if len(history) == 1:
            return history[-1] - 0.5  # delta from baseline
    except (OSError, json.JSONDecodeError, IndexError):
        pass
    return 0.0


def _git_velocity(hours: float = 1.0) -> tuple[int, int, float]:
    """Derive (issues_completed, issues_attempted, elapsed_hours) from git log.

    Uses commit timestamps within the last `hours` window.
    When >= 2 commits exist, elapsed = time from oldest to newest commit.
    """
    try:
        since = f"{int(hours)} hour ago" if hours <= 1.0 else f"{int(hours)} hours ago"
        result = subprocess.run(
            ["git", "log", f"--since={since}", "--no-merges", "--format=%ci"],
            capture_output=True, text=True, timeout=10,
            cwd=str(CLAUDE_DIR),
        )
        if result.returncode == 0 and result.stdout.strip():
            lines = [line.strip() for line in result.stdout.strip().split("\n") if line.strip()]
            count = len(lines)
            if count >= 2:
                from datetime import datetime as dt
                oldest = dt.fromisoformat(lines[-1].split("+")[0].strip())
                newest = dt.fromisoformat(lines[0].split("+")[0].strip())
                elapsed = max((newest - oldest).total_seconds() / 3600, 0.1)
            else:
                elapsed = hours
            return count, max(count, 1), elapsed
    except (subprocess.TimeoutExpired, FileNotFoundError, Exception):
        pass
    return 0, 1, hours


def _save_delight_snapshot(snap: DelightSnapshot) -> None:
    """Append snapshot to learning/delight_snapshots.jsonl."""
    try:
        DELIGHT_SNAPSHOTS_FILE.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "timestamp": snap.timestamp,
            "composite": round(snap.composite, 4),
            "user_delight": round(snap.user_delight, 4),
            "system_health": round(snap.system_health, 4),
            "flow_state": round(snap.flow_state, 4),
            "velocity": round(snap.velocity, 4),
            "zero_interruption_streak": snap.zero_interruption_streak,
            "completion_ratio": round(snap.completion_ratio, 4),
            "genome_fitness_trend": round(snap.genome_fitness_trend, 4),
            "converge_velocity": round(snap.converge_velocity, 4),
            "pattern_discovery_count": snap.pattern_discovery_count,
            "kono_diversity": round(snap.kono_diversity, 4),
        }
        with open(DELIGHT_SNAPSHOTS_FILE, "a") as f:
            f.write(json.dumps(record) + "\n")
    except (OSError, Exception):
        pass


def _estimate_converge_velocity(cs: CycleState) -> float:
    """Estimate converge velocity from cycle state or metrics fallback."""
    if cs.velocity_history:
        latest_vel = cs.velocity_history[-1]
        return latest_vel.get("velocity", 0)
    # Fallback: read from converge metrics files
    try:
        metrics_files = [
            CLAUDE_DIR / "learning" / "converge-metrics.jsonl",
            CLAUDE_DIR / "metrics.jsonl",
        ]
        velocities = []
        for metrics_file in metrics_files:
            if not metrics_file.exists():
                continue
            for line in metrics_file.read_text().strip().split("\n")[-20:]:
                try:
                    entry = json.loads(line)
                    if entry.get("event") == "converge_end":
                        vt = entry.get("velocity_trajectory", [])
                        if len(vt) >= 2:
                            velocities.append(vt[-1] - vt[-2])
                except json.JSONDecodeError:
                    continue
        if velocities:
            return max(0, sum(velocities) / len(velocities) / 100)
    except Exception as _e:
        logger.debug("Converge velocity estimation failed: %s", _e)
    return 0.0


def _count_new_patterns(elapsed_hours: float) -> int:
    """Count new patterns from insights.jsonl and recent git commits."""
    count = 0
    try:
        insights_file = CLAUDE_DIR / "learning" / "insights.jsonl"
        if insights_file.exists():
            cutoff = (datetime.now(timezone.utc) - timedelta(hours=max(elapsed_hours, 8))).isoformat()
            for line in insights_file.read_text().strip().split("\n"):
                try:
                    e = json.loads(line)
                    ts = e.get("timestamp", e.get("created", ""))
                    if ts >= cutoff:
                        count += 1
                except (json.JSONDecodeError, ValueError):
                    continue
    except Exception as _e:
        logger.debug("Insights pattern scan failed: %s", _e)
    # Also count novel commit types (feat/refactor) as lightweight patterns
    try:
        from lib.subprocess_utils import run_git as _rg
        since = (datetime.now(timezone.utc) - timedelta(hours=max(elapsed_hours, 8))).isoformat()
        commits_raw = _rg(["log", f"--since={since}", "--format=%s", "--no-merges"], timeout=10)
        if commits_raw:
            for msg in commits_raw.strip().split("\n"):
                if msg.startswith(("feat:", "refactor:", "perf:")):
                    count += 1
    except Exception as _e:
        logger.debug("Pattern detection from git failed: %s", _e)
    return count


def _measure_kono_diversity() -> float:
    """Measure KONO dimension entropy as a diversity score.

    Returns normalised entropy (0.0-1.0), or 0.0 on failure.
    """
    try:
        import subprocess as _sp
        _kono_raw = _sp.run(
            [sys.executable, str(CLAUDE_DIR / "lib" / "kono_substrate.py"), "stats"],
            capture_output=True, text=True, timeout=10,
        )
        if _kono_raw.returncode == 0:
            _kono_stats = json.loads(_kono_raw.stdout)
            _emb = _kono_stats.get("embeddings", {})
            _by_dim = _emb.get("by_dimension", {})
            _total = _emb.get("total", 0)
            if isinstance(_by_dim, dict) and _by_dim and _total > 0:
                import math
                probs = [v / _total for v in _by_dim.values() if isinstance(v, (int, float)) and v > 0]
                max_entropy = math.log(max(len(probs), 1)) if probs else 1
                entropy = -sum(p * math.log(p) for p in probs if p > 0)
                return entropy / max(max_entropy, 0.001)
    except Exception as _e:
        logger.debug("KONO diversity check failed: %s", _e)
    return 0.0


def _store_delight_to_kono(snap: DelightSnapshot) -> None:
    """Store delight snapshot to KONO for cross-session trend analysis.

    Silent on failure — KONO storage is best-effort.
    """
    try:
        from lib.kono_substrate import MemorySubstrate
        kono = MemorySubstrate()
        content = (
            f"delight composite={snap.composite:.3f} "
            f"user={snap.user_delight:.3f} system={snap.system_health:.3f} "
            f"flow={snap.flow_state:.2f} velocity={snap.velocity:.1f}/hr "
            f"completion={snap.completion_ratio:.2f} "
            f"genome_trend={snap.genome_fitness_trend:.3f} "
            f"patterns={snap.pattern_discovery_count}"
        )
        kono.store(
            domain="convergence-delight",
            key=f"delight-{snap.timestamp[:10]}",
            content=content,
        )
    except (ImportError, Exception):
        pass


def delight_snapshot(
    issues_completed: int = 0,
    issues_attempted: int = 0,
    gate_blocks: int = 0,
    total_ops: int = 0,
    elapsed_hours: float = 1.0,
    new_patterns: int = 0,
) -> DelightSnapshot:
    """Capture a delight snapshot from current session state.

    Called during breathing pauses between Fibonacci waves.
    Also callable standalone: python3 lib/auto_converge.py delight

    Args:
        issues_completed: Issues resolved this session
        issues_attempted: Issues attempted this session (auto-detected from git if 0)
        gate_blocks: Gate denials this session
        total_ops: Total tool operations this session
        elapsed_hours: Session window for git velocity scan
        new_patterns: Novel patterns discovered this session
    """
    snap = DelightSnapshot(
        timestamp=datetime.now(timezone.utc).isoformat(),
    )

    # Auto-detect velocity from git log when issues not supplied
    if issues_completed == 0 and issues_attempted == 0:
        issues_completed, issues_attempted, elapsed_hours = _git_velocity(elapsed_hours)

    # User signals
    snap.flow_state = max(0, 1 - (gate_blocks / max(total_ops, 1)))
    snap.velocity = issues_completed / max(elapsed_hours, 0.1)
    snap.zero_interruption_streak = max(0, total_ops - gate_blocks)
    snap.completion_ratio = issues_completed / max(issues_attempted, 1)

    # Genome fitness trend
    snap.genome_fitness_trend = _genome_fitness_trend()

    # Converge velocity
    cs = load_cycle_state()
    snap.converge_velocity = _estimate_converge_velocity(cs)

    # Pattern discovery
    if new_patterns == 0:
        new_patterns = _count_new_patterns(elapsed_hours)
    snap.pattern_discovery_count = new_patterns

    # KONO diversity
    snap.kono_diversity = _measure_kono_diversity()

    snap.compute()
    _save_delight_snapshot(snap)
    _store_delight_to_kono(snap)

    return snap


# =========================================================================
# Velocity Report — for pair-mode and MCP apps
# =========================================================================

def velocity_report() -> dict:
    """Generate velocity report from stored history.

    Returns dict with current velocity, trend, and recommendations.
    Used by pair-mode session monitoring and MCP apps.
    """
    cs = load_cycle_state()
    vh = cs.velocity_history

    if not vh:
        return {
            "status": "no_data",
            "message": "No velocity data recorded yet. Run a convergence cycle first.",
            "velocity": 0.0,
            "acceleration": 0.0,
            "trend": "unknown",
            "recommendation": "Run: python3 lib/auto_converge.py run",
        }

    latest = vh[-1]
    velocities = [v.get("velocity", 0) for v in vh[-10:]]
    scores = [v.get("score", 0) for v in vh[-10:]]

    # Determine trend
    if len(velocities) >= 3:
        recent_avg = sum(velocities[-3:]) / 3
        older_avg = sum(velocities[:3]) / max(len(velocities[:3]), 1)
        if recent_avg > older_avg * 1.1:
            trend = "accelerating"
        elif recent_avg < older_avg * 0.5:
            trend = "decelerating"
        else:
            trend = "steady"
    else:
        trend = "insufficient_data"

    # Recommendation
    if latest.get("is_stalling"):
        recommendation = "Stall detected. Switch strategy or review blocker."
    elif trend == "accelerating":
        recommendation = "Velocity increasing. Continue current approach."
    elif trend == "decelerating":
        recommendation = "Velocity dropping. Consider alternative improvement strategies."
    else:
        recommendation = "Steady progress. Monitor for stalls."

    # Include delight data if available
    dt = delight_trend(days=7)
    delight_section = {}
    if dt.get("status") == "ok":
        delight_section = {
            "delight_composite": dt["composite_avg"],
            "delight_trend": dt["trend"],
            "delight_user": dt["user_delight_avg"],
            "delight_system": dt["system_health_avg"],
        }

    return {
        "status": "ok",
        "velocity": latest.get("velocity", 0),
        "acceleration": latest.get("acceleration", 0),
        "is_stalling": latest.get("is_stalling", False),
        "current_score": latest.get("score", 0),
        "trend": trend,
        "data_points": len(vh),
        "scores_history": scores,
        "velocities_history": velocities,
        "recommendation": recommendation,
        "best_score": cs.best_score,
        "total_cycles": cs.total_cycles,
        **delight_section,
    }


# =========================================================================
# Delight Trend Analysis — rolling 30-day averages (#315)
# =========================================================================

def delight_trend(days: int = 30) -> dict:
    """Load delight history and compute rolling trend.

    Returns dict with:
    - snapshots: raw snapshot count
    - composite_avg: rolling average of composite delight
    - user_delight_avg: rolling average of user delight
    - system_health_avg: rolling average of system health
    - trend: "improving", "declining", "stable", or "no_data"
    - best/worst composite and their dates
    """
    if not DELIGHT_SNAPSHOTS_FILE.exists():
        return {"status": "no_data", "message": "No delight snapshots yet."}

    try:
        raw_text = DELIGHT_SNAPSHOTS_FILE.read_text()
    except (UnicodeDecodeError, OSError):
        return {"status": "no_data", "message": "Delight snapshots file unreadable (encrypted or corrupted)."}

    snapshots = []
    cutoff = datetime.now(timezone.utc).isoformat()[:10]
    for line in raw_text.strip().split("\n"):
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
            snapshots.append(entry)
        except json.JSONDecodeError:
            continue

    if not snapshots:
        return {"status": "no_data", "message": "No valid delight snapshots."}

    # Last N days only
    recent = snapshots[-days:] if len(snapshots) > days else snapshots

    composites = [s.get("composite", 0) for s in recent]
    user_ds = [s.get("user_delight", 0) for s in recent]
    sys_hs = [s.get("system_health", 0) for s in recent]

    avg = lambda lst: sum(lst) / len(lst) if lst else 0  # noqa: E731

    # Trend: compare first half to second half
    if len(composites) >= 4:
        mid = len(composites) // 2
        first_half = avg(composites[:mid])
        second_half = avg(composites[mid:])
        delta = second_half - first_half
        if delta > 0.05:
            trend = "improving"
        elif delta < -0.05:
            trend = "declining"
        else:
            trend = "stable"
    elif len(composites) >= 2:
        trend = "improving" if composites[-1] > composites[0] else "declining"
    else:
        trend = "insufficient_data"

    best_idx = max(range(len(composites)), key=lambda i: composites[i])
    worst_idx = min(range(len(composites)), key=lambda i: composites[i])

    return {
        "status": "ok",
        "snapshots": len(recent),
        "composite_avg": round(avg(composites), 4),
        "user_delight_avg": round(avg(user_ds), 4),
        "system_health_avg": round(avg(sys_hs), 4),
        "trend": trend,
        "best_composite": round(composites[best_idx], 4),
        "best_date": recent[best_idx].get("timestamp", "")[:10],
        "worst_composite": round(composites[worst_idx], 4),
        "worst_date": recent[worst_idx].get("timestamp", "")[:10],
        "latest": recent[-1] if recent else {},
    }


# =========================================================================
# CLI
# =========================================================================


def _cmd_status() -> int:
    cs = load_cycle_state()
    print(f"Total cycles: {cs.total_cycles}")
    print(f"Cycles today: {cs.cycles_today}/{MAX_CYCLES_PER_DAY}")
    print(f"Best score: {cs.best_score:.1f}")
    if cs.history:
        last = cs.history[-1]
        print(f"Last cycle: {last['timestamp']}, depth={last['depth']}, "
              f"satisfied={last['satisfied']}, score={last['score']:.1f}")
    return 0


def _cmd_check() -> int:
    quick = "--full" not in sys.argv
    state = measure_current_state(quick=quick)
    met = rsi_criterion(state)
    print(f"Criterion met: {met}")
    if state.test_rate_measured:
        print(f"  test_pass_rate: {state.test_pass_rate:.1%} (need >= 95%)")
    else:
        print("  test_pass_rate: UNMEASURED (need >= 95%) — no cached rate and "
              "the suite did not report; run with --full, or raise "
              "GRIP_RSI_PYTEST_TIMEOUT if it needs longer")
    if state.review_measured:
        print(f"  reviews: {state.ralph_avg:.1f}% of merged pull requests passed AGORA "
              "at the first sitting, last 7 days (need >= 85%)")
    else:
        why = _cached_review_reason() or "no full check in the last 24 hours measured a rate"
        print(f"  reviews: UNMEASURED (need >= 85%) -- {why}; run `check --full`")
    if state.meta_work_measured:
        print(f"  meta_work_pct: {state.meta_work_pct:.1%} (shown only; not in the "
              "criterion, GRIP#9668)")
    else:
        print("  meta_work_pct: not recorded (nothing writes it; not in the "
              "criterion until something does, GRIP#9668)")
    print(f"  stall_detected: {state.stall_detected}")
    unmeasured = unmeasured_inputs(state)
    if unmeasured:
        # Same list the criterion fails closed on, so the status output and the
        # convergence decision can never disagree about what was measured.
        print(f"  criterion: NOT MET — {len(unmeasured)} of 2 inputs unmeasured "
              f"({', '.join(unmeasured)}); absent evidence is not a passing grade")
        # Say so rather than print a number built from absent evidence.
        print(f"  composite_score: {state.score:.1f}/100 — NOT MEANINGFUL, "
              f"{len(unmeasured)} of 2 inputs unmeasured ({', '.join(unmeasured)})")
    else:
        print(f"  composite_score: {state.score:.1f}/100")
    return 0 if met else 1


def _cmd_measure() -> int:
    quick = "--quick" in sys.argv
    state = measure_current_state(quick=quick)
    print(json.dumps(asdict(state), indent=2))
    return 0


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: auto_converge.py <command>")
        print("Commands: status, check, run, measure, velocity, delight, warmup")
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "status":
        sys.exit(_cmd_status())

    elif cmd == "check":
        sys.exit(_cmd_check())

    elif cmd == "measure":
        sys.exit(_cmd_measure())

    elif cmd == "velocity":
        report = velocity_report()
        if report["status"] == "no_data":
            print(f"[VELOCITY] {report['message']}")
        else:
            print(f"[VELOCITY] Current: {report['velocity']:.3f} (accel: {report['acceleration']:.3f})")
            print(f"[VELOCITY] Trend: {report['trend']}")
            print(f"[VELOCITY] Score: {report['current_score']:.1f} | Best: {report['best_score']:.1f}")
            print(f"[VELOCITY] Stalling: {report['is_stalling']}")
            print(f"[VELOCITY] Data points: {report['data_points']} | Total cycles: {report['total_cycles']}")
            print(f"[VELOCITY] Recommendation: {report['recommendation']}")
        sys.exit(0)

    elif cmd == "delight":
        # Parse args in two formats:
        #   --completed N --attempted N    (flag format)
        #   completed=9 attempted=11       (key=value format)
        kwargs: dict = {}
        KEY_MAP = {
            "completed": ("issues_completed", int),
            "attempted": ("issues_attempted", int),
            "blocks": ("gate_blocks", int),
            "ops": ("total_ops", int),
            "hours": ("elapsed_hours", float),
            "patterns": ("new_patterns", int),
        }
        args = sys.argv[2:]
        i = 0
        while i < len(args):
            arg = args[i]
            # key=value format
            if "=" in arg:
                key, _, val = arg.partition("=")
                key = key.lstrip("-").replace("-", "_")
                if key in KEY_MAP:
                    param_name, cast = KEY_MAP[key]
                    try:
                        kwargs[param_name] = cast(val)
                    except ValueError:
                        pass
                i += 1
            # --key value format
            elif arg.startswith("-"):
                key = arg.lstrip("-").replace("-", "_")
                if key in KEY_MAP and i + 1 < len(args):
                    param_name, cast = KEY_MAP[key]
                    try:
                        kwargs[param_name] = cast(args[i + 1])
                    except (ValueError, IndexError):
                        pass
                    i += 2
                else:
                    i += 1
            else:
                i += 1
        snap = delight_snapshot(**kwargs)
        print(f"[DELIGHT] Composite: {snap.composite:.3f}")
        print(f"  User delight:  {snap.user_delight:.3f} "
              f"(flow={snap.flow_state:.2f}, vel={snap.velocity:.1f}/hr, "
              f"streak={snap.zero_interruption_streak}, ratio={snap.completion_ratio:.2f})")
        print(f"  System health: {snap.system_health:.3f} "
              f"(genome={snap.genome_fitness_trend:.3f}, converge_vel={snap.converge_velocity:.3f}, "
              f"patterns={snap.pattern_discovery_count}, kono_div={snap.kono_diversity:.2f})")
        sys.exit(0)

    elif cmd == "warmup":
        # Bootstrap converge velocity signal with a minimal metrics record
        from converge_metrics import record_start, record_depth, record_end

        run_id = record_start(
            task="warmup — bootstrap velocity signal",
            task_type="general",
            max_depth=1,
        )
        record_depth(run_id, depth=0,
            dimension_scores={"quality": 85, "completeness": 80, "conciseness": 90},
            weaknesses=["initial warmup — no real task"],
            strategy="bootstrap metrics to enable velocity tracking",
            deliverables=[])
        record_end(run_id, satisfied=True, depth_reached=0,
            elapsed_minutes=0.1, commits=0,
            velocity_trajectory=[80, 85],
            dimension_scores={"quality": 85, "completeness": 80, "conciseness": 90},
            invoked_genes=["session-state-persistence"])
        print("[WARMUP] Converge velocity signal bootstrapped.")
        sys.exit(0)

    elif cmd == "delight-trend":
        days = 30
        if len(sys.argv) > 2:
            try:
                days = int(sys.argv[2])
            except ValueError:
                pass
        dt = delight_trend(days=days)
        if dt.get("status") == "no_data":
            print(f"[DELIGHT] {dt['message']}")
        else:
            print(f"[DELIGHT] Trend: {dt['trend']} ({dt['snapshots']} snapshots, {days}d window)")
            print(f"  Composite avg:     {dt['composite_avg']:.4f}")
            print(f"  User delight avg:  {dt['user_delight_avg']:.4f}")
            print(f"  System health avg: {dt['system_health_avg']:.4f}")
            print(f"  Best:  {dt['best_composite']:.4f} ({dt['best_date']})")
            print(f"  Worst: {dt['worst_composite']:.4f} ({dt['worst_date']})")
        sys.exit(0)

    elif cmd == "run":
        dry_run = "--live" not in sys.argv
        result = run_cycle(dry_run=dry_run)
        if result.satisfied:
            print("[AUTO-CONVERGE] Criterion satisfied!")
        else:
            print(f"[AUTO-CONVERGE] Not yet converged (score: {result.best_state.score:.1f})")
        sys.exit(0 if result.satisfied else 1)

    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)


if __name__ == "__main__":
    main()
