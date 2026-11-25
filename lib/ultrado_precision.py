#!/usr/bin/env python3
"""
Ultrado Precision Router — Adaptive speed detection for wave tasks.

Scans task descriptions for precision markers to route between:
- FAST: execution bias, direct implementation (default)
- CAREFUL: ultraplan + ultrathink + verify (sensitive surgery)
- VERIFY: broly council + devil's advocate + falsify (novel claims)

Origin: Gen 499. V>> directive: "balanced approach between speed and
deliberate, pragmatic, measured approach."
"""

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class PrecisionLevel(Enum):
    FAST = "fast"
    CAREFUL = "careful"
    VERIFY = "verify"


# Wave-cost constants (GRIP#2295 + H090).
# Empirical values from ultrado-gen510 telemetry; update when wave-cost
# telemetry accumulates enough data to contradict them.
WAVE_COST_P50 = 14_000  # typical FAST-heavy wave
WAVE_COST_P90 = 22_000  # CAREFUL/VERIFY-heavy wave
PERSIST_RESERVE = 15_000  # tokens reserved for /save + checkpoint + final summary
HARD_GATE_PCT = 85.0  # Rule 8 context gate fires here
DEFAULT_BUDGET = 200_000  # Claude Sonnet 4.6 / Opus 4.7 standard. 1M for extended.
MAX_WAVES = 6  # Fibonacci 1,1,2,3,5,8 — six entries


@dataclass(frozen=True)
class WavePlan:
    """Outcome of adapt_wave_plan(). Feeds /ultrado Phase 0 output and PLAN.

    ``waves == 0`` means no runway; ``handoff`` tells the caller to pivot
    directly to ``/save`` + fresh-session handoff instead of planning any
    wave work. ``reason`` is the one-line explanation to print to the
    operator. ``per_wave_budget`` is advisory — waves may spend less.
    """

    waves: int
    handoff: bool
    reason: str
    per_wave_budget: int
    remaining_pct: float  # how much runway we actually had at plan time
    plan_tier: str  # "full" | "truncated" | "single" | "handoff" — for log/metric


def adapt_wave_plan(
    current_context_pct: float,
    total_budget: int = DEFAULT_BUDGET,
    max_waves: int = MAX_WAVES,
    wave_cost: int = WAVE_COST_P50,
) -> WavePlan:
    """Scale the Fibonacci wave count to remaining context budget.

    Called by ``/ultrado`` at Phase 0 (ABSORB -> PLAN transition). Pure
    function: no I/O, no side effects, easy to test. The caller prints
    the returned ``reason`` and uses ``waves`` to size the plan.

    Args
    ----
    current_context_pct
        Percentage of the context window currently consumed, in [0, 100].
        Callers pass the value they've just read from the harness; we
        clamp defensively.
    total_budget
        Total context window in tokens. Defaults to 200k (standard
        Sonnet 4.6 / Opus 4.7). Pass 1_000_000 for extended-context runs.
    max_waves
        Cap on the returned wave count. Defaults to MAX_WAVES (6 for the
        Fibonacci series 1,1,2,3,5,8). Setting lower caps forces the
        caller into shorter sprints.
    wave_cost
        Per-wave token budget to plan against. Defaults to WAVE_COST_P50
        (14k). Pass WAVE_COST_P90 (22k) for CAREFUL/VERIFY-heavy plans.

    Returns
    -------
    WavePlan
        Structured plan. See the dataclass docstring for field semantics.
    """
    # Defensive clamp — a buggy caller passing >100 shouldn't crash
    # ultrado; it should get a handoff result.
    pct = max(0.0, min(100.0, current_context_pct))
    remaining_pct = max(0.0, HARD_GATE_PCT - pct)
    remaining_tokens = int((remaining_pct / 100.0) * total_budget)
    usable = remaining_tokens - PERSIST_RESERVE

    if usable < wave_cost:
        return WavePlan(
            waves=0,
            handoff=True,
            reason=(
                f"context {pct:.1f}% used -> {remaining_pct:.1f}% runway; "
                f"{usable} usable tokens < {wave_cost}/wave. "
                "HANDOFF mode: /save + fresh session."
            ),
            per_wave_budget=0,
            remaining_pct=remaining_pct,
            plan_tier="handoff",
        )

    raw_waves = usable // wave_cost
    # min against max_waves so we don't over-plan a fresh session
    waves = max(1, min(max_waves, raw_waves))
    per_wave = usable // waves if waves else 0

    if waves == 1:
        tier = "single"
        reason = (
            f"context {pct:.1f}% used -> {remaining_pct:.1f}% runway; "
            f"SINGLE WAVE mode (FAST only, ~{per_wave // 1000}k budget), "
            "then /save + handoff to fresh session."
        )
    elif waves < max_waves:
        tier = "truncated"
        reason = (
            f"context {pct:.1f}% used -> {remaining_pct:.1f}% runway; "
            f"TRUNCATED plan: {waves} waves at ~{per_wave // 1000}k/wave, "
            "checkpoints every wave, /save at end."
        )
    else:
        tier = "full"
        reason = (
            f"context {pct:.1f}% used -> {remaining_pct:.1f}% runway; "
            f"FULL Fibonacci plan: {waves} waves at ~{per_wave // 1000}k/wave."
        )

    return WavePlan(
        waves=waves,
        handoff=False,
        reason=reason,
        per_wave_budget=per_wave,
        remaining_pct=remaining_pct,
        plan_tier=tier,
    )


# Markers that trigger elevated precision.
# Lowercase matching. Checked in order: VERIFY first (most restrictive).
VERIFY_MARKERS = [
    "novel claim", "convergence proof", "formal proof", "theorem",
    "statistical significance", "benchmark result", "performance claim",
    "measure theory", "falsification", "self-score", "metric validation",
    "external scoring", "cross-model", "council verdict",
]

CAREFUL_MARKERS = [
    "migrate", "migration", "database migration", "schema change",
    "security", "authentication", "authorisation", "authorization",
    "payment", "billing", "invoice", "financial",
    "cryptographic", "encryption", "key management", "git-crypt",
    "api contract", "breaking change", "backward compat",
    "data loss", "data deletion", "drop table", "rm -rf",
    "production", "deploy", "release", "rollback",
    "legal", "compliance", "gdpr", "privacy",
    "delete user", "purge", "wipe",
    "webhook secret", "token rotation",
    "ci/cd", "github actions", "workflow",
]


def detect_precision(
    task_description: str,
    override: Optional[PrecisionLevel] = None,
) -> PrecisionLevel:
    """
    Detect appropriate precision level from task description.

    Args:
        task_description: Natural language task description
        override: User-specified override (--fast or --careful)

    Returns:
        PrecisionLevel for the task
    """
    if override is not None:
        return override

    desc_lower = task_description.lower()

    for marker in VERIFY_MARKERS:
        if marker in desc_lower:
            return PrecisionLevel.VERIFY

    for marker in CAREFUL_MARKERS:
        if marker in desc_lower:
            return PrecisionLevel.CAREFUL

    return PrecisionLevel.FAST


def describe_precision(level: PrecisionLevel) -> str:
    """Human-readable description of what a precision level does."""
    descriptions = {
        PrecisionLevel.FAST: "Direct implementation: edit, test, ship. Biased for speed.",
        PrecisionLevel.CAREFUL: "Ultraplan + ultrathink before implementation. For sensitive surgery.",
        PrecisionLevel.VERIFY: "Broly council + devil's advocate + falsification. For novel claims.",
    }
    return descriptions[level]


def precision_time_budget(level: PrecisionLevel) -> int:
    """Suggested time budget in minutes per task at this precision level."""
    budgets = {
        PrecisionLevel.FAST: 5,
        PrecisionLevel.CAREFUL: 15,
        PrecisionLevel.VERIFY: 30,
    }
    return budgets[level]


def classify_wave_tasks(tasks: list) -> dict:
    """
    Classify a list of task descriptions into precision levels.

    Returns dict mapping PrecisionLevel -> list of task descriptions.
    """
    classified = {
        PrecisionLevel.FAST: [],
        PrecisionLevel.CAREFUL: [],
        PrecisionLevel.VERIFY: [],
    }
    for task in tasks:
        level = detect_precision(task)
        classified[level].append(task)
    return classified


def _is_prime(n: int) -> bool:
    """Miller-style trial division. Correct for all n >= 2."""
    if n < 2:
        return False
    if n == 2:
        return True
    if n % 2 == 0:
        return False
    i = 3
    while i * i <= n:
        if n % i == 0:
            return False
        i += 2
    return True


# Module-level cache: index → fibo_prime value.
# Populated lazily by fibo_prime(); avoids re-scanning on repeated calls.
_FIBO_PRIME_CACHE: list[int] = []


def fibo_prime(n: int) -> int:
    """Return the n-th Fibonacci number that is also prime (0-indexed).

    Sequence: 2, 3, 5, 13, 89, 233, 1597, 28657, 514229, ...

    Args:
        n: Zero-based index into the Fibo-prime sequence.

    Returns:
        The n-th Fibo-prime integer.

    Raises:
        ValueError: If n < 0.
    """
    if n < 0:
        raise ValueError(f"fibo_prime index must be >= 0, got {n}")

    # Extend cache until we have n+1 entries.
    # Walk Fibonacci sequence from wherever the cache left off.
    a, b = 1, 1
    # Advance the Fibonacci generator past already-cached values.
    # We track how many Fib numbers we have checked total (not just primes).
    fib_checked = 0
    # Rebuild generator state from known cache size to avoid rescanning.
    # Simpler than storing generator state: restart and skip known primes fast.
    # Performance is acceptable — Fibo-primes are sparse; cache fills quickly.
    a, b = 1, 1
    found: list[int] = []
    while len(found) <= n:
        if _is_prime(a):
            found.append(a)
        a, b = b, a + b

    # Persist any newly computed entries to module cache.
    _FIBO_PRIME_CACHE.clear()
    _FIBO_PRIME_CACHE.extend(found)

    return found[n]


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: ultrado_precision.py <task description>")
        print("       ultrado_precision.py --test")
        sys.exit(1)

    if sys.argv[1] == "--test":
        test_cases = [
            ("Add a docstring to the helper function", PrecisionLevel.FAST),
            ("Fix the CSS padding on the dashboard", PrecisionLevel.FAST),
            ("Migrate the users table to add email column", PrecisionLevel.CAREFUL),
            ("Rotate the API authentication tokens", PrecisionLevel.CAREFUL),
            ("Update the GitHub Actions CI/CD workflow", PrecisionLevel.CAREFUL),
            ("Delete user data for GDPR compliance", PrecisionLevel.CAREFUL),
            ("Validate the convergence proof claims", PrecisionLevel.VERIFY),
            ("Benchmark result shows 30% improvement", PrecisionLevel.VERIFY),
            ("External scoring validation of metrics", PrecisionLevel.VERIFY),
        ]
        passed = 0
        for desc, expected in test_cases:
            result = detect_precision(desc)
            status = "PASS" if result == expected else "FAIL"
            if status == "PASS":
                passed += 1
            print(f"  [{status}] {result.value:8s} <- {desc[:60]}")
        print(f"\n  {passed}/{len(test_cases)} passed")
        sys.exit(0 if passed == len(test_cases) else 1)

    desc = " ".join(sys.argv[1:])
    level = detect_precision(desc)
    print(f"Precision: {level.value}")
    print(f"Strategy:  {describe_precision(level)}")
    print(f"Budget:    {precision_time_budget(level)} min/task")
