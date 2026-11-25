"""Adaptive effort selector — pure deterministic tier function.

Cheap-by-determinism (no LLM calls, no I/O). All weights and thresholds
are named module constants so mutmut mutations on them break tests.

Hypotheses anchored: H705 (mutmut ≥ 80% kill rate).
"""
from __future__ import annotations

from enum import Enum


class EffortTier(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    XHIGH = "xhigh"
    MAX = "max"


# --- Named score weights (mutmut-anchor: flipping sign breaks ≥1 test) ---
_W_NOVELTY = 0.30
_W_CLAIM = 0.20
_W_CODE = 0.15
_W_PATTERN = 0.15
_W_HYP = 0.10
_W_LENGTH = 0.10

# --- Pattern scores ---
_PATTERN_SCORES: dict[str, float] = {
    "FullMesh": 4.0,
    "Sprint": 3.0,
    "Focused": 2.0,
    "Direct": 1.0,
}
_PATTERN_DEFAULT = 1.0

# --- Tier thresholds ---
_MAX_THRESHOLD = 3.5
_XHIGH_THRESHOLD = 2.5
_HIGH_THRESHOLD = 1.5
_MEDIUM_THRESHOLD = 0.5

# --- Trivial fast-path gate ---
_TRIVIAL_MAX_LEN = 80
_BUDGET_FLOOR = 50_000
_RATE_CALM_THRESHOLD = 0.5

# --- Budget pressure multiplier ---
_BUDGET_PRESSURE_MULTIPLIER = 0.6

# --- Claim-density keywords ---
_CLAIM_KEYWORDS = ("must", "never", "always", "guarantee", "invariant", "prove")


def score_components(prompt: str, kernel_keys: set[str]) -> dict[str, float | int]:
    """Public helper for components used in rationale strings.

    AGORA-4 fix: orchestrator no longer imports `_novelty_score` /
    `_CLAIM_KEYWORDS` privately. Returns the deterministic novelty +
    claim-density used to build the IDR rationale digest.
    """
    novel = _novelty_score(prompt, kernel_keys)
    claim_dens = sum(prompt.lower().count(w) for w in _CLAIM_KEYWORDS)
    return {"novelty": novel, "claim_density": claim_dens}


def _novelty_score(prompt: str, kernel_keys: set[str]) -> float:
    """Jaccard distance between prompt token set and kernel anchor set.

    Returns value in [0, 1]. Empty kernel → 1.0 (fully novel).
    Empty prompt → 0.0 (nothing to compare).
    """
    if not prompt:
        return 0.0
    prompt_tokens = set(prompt.lower().split())
    if not kernel_keys:
        return 1.0
    intersection = prompt_tokens & kernel_keys
    union = prompt_tokens | kernel_keys
    return 1.0 - len(intersection) / len(union)


def adaptive_effort_selector(
    prompt: str,
    kernel_keys: set[str],
    routing_hint: dict,
    budget: dict,
    hyp_backlog: int,
) -> EffortTier:
    """Select effort tier for an Opus precog turn.

    Args:
        prompt: Raw user prompt text.
        kernel_keys: Normalised section anchors from HOLISTIC kernel.
        routing_hint: Output of prompt-routing-hook (may be {} — fail-open).
        budget: {tokens_remaining: int, rate_limit_proximity: float 0..1}.
        hyp_backlog: Count of pending hypotheses in learning/hypotheses.jsonl.

    Returns:
        EffortTier enum value.
    """
    length = len(prompt)
    code_dens = prompt.count("```") + prompt.count("def ") + prompt.count("class ")
    claim_dens = sum(prompt.lower().count(w) for w in _CLAIM_KEYWORDS)
    novel = _novelty_score(prompt, kernel_keys)
    pattern = routing_hint.get("recommended_pattern", "Focused")
    budget_ok = budget.get("tokens_remaining", 0) > _BUDGET_FLOOR
    rate_calm = budget.get("rate_limit_proximity", 0) < _RATE_CALM_THRESHOLD

    # Trivial fast-path (dispatch-economics depth-0)
    if (length < _TRIVIAL_MAX_LEN
            and code_dens == 0
            and claim_dens == 0
            and pattern == "Direct"):
        return EffortTier.LOW

    pattern_score = _PATTERN_SCORES.get(pattern, _PATTERN_DEFAULT)

    score = (
        _W_NOVELTY * min(novel * 4, 4)
        + _W_CLAIM * min(claim_dens, 4)
        + _W_CODE * min(code_dens, 4)
        + _W_PATTERN * pattern_score
        + _W_HYP * min(hyp_backlog / 5, 4)
        + _W_LENGTH * min(length / 500, 4)
    )

    if not budget_ok or not rate_calm:
        score *= _BUDGET_PRESSURE_MULTIPLIER

    if score >= _MAX_THRESHOLD:
        return EffortTier.MAX
    if score >= _XHIGH_THRESHOLD:
        return EffortTier.XHIGH
    if score >= _HIGH_THRESHOLD:
        return EffortTier.HIGH
    if score >= _MEDIUM_THRESHOLD:
        return EffortTier.MEDIUM
    return EffortTier.LOW
