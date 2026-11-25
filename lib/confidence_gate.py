"""Confidence Gate — "How sure are you?" measurement via HAL council.

Three-tier cascade that measures confidence in tool calls and returns
a permissionDecision when confidence falls below threshold.

Tier 0 (Heuristic, <5ms):
    Pattern-based uncertainty detection from transcript context.
    Catches hedging language, repeated tool retries, question marks.
    Runs on EVERY tool call. Returns 0.0-1.0 confidence estimate.

Tier 1 (Fast Council, ~2-5s):
    Two-provider quick evaluation via HAL external_score(mode="fast").
    Gemini 2.5 Flash + GPT-OSS 120B only. Fires when Tier 0 returns < 0.7.

Tier 2 (Full Council, ~10-30s):
    Multi-provider weighted median via HAL external_score(mode="standard").
    3 providers (4 with GRIP_CLAUDE_ALLOW_PAID=1) with KS divergence
    detection. Fires when Tier 1 returns
    borderline (0.5-0.7) or when providers disagree (KS > 0.3).

Design principle: "Judges judging judges, with falsifiability in mind."
(Origin: ACG Guild H-001 discussion, 2026-04-04)

The heuristic is transparent about being a heuristic. The council is
transparent about being a council. GRIP never claims certainty it
doesn't have.

Hook integration:
    This module is called from context-gate-hook.py as _gate_confidence().
    It does NOT run the council on every tool call — only escalates when
    the fast heuristic detects uncertainty signals.

Usage:
    from confidence_gate import measure_confidence, ConfidenceResult

    result = measure_confidence(
        tool_name="Bash",
        tool_input={"command": "rm -rf /tmp/old"},
        transcript_context="I think this might work...",
    )
    if not result.passed:
        return deny(result.reason)
"""

import logging
import os
import re
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# ── Configuration ────────────────────────────────────────────────────────

# Default threshold for all three tiers when no env override is set.
# 0.70 was the operational baseline before D3 (TF>> liberation matrix,
# 2026-04-20). Rule 7's doctrinal "99.9999999%" target lives in
# rules/efficiency-rules.md — that's the aspiration; this is what
# the runtime can actually measure.
_DEFAULT_THRESHOLD = 0.70


def _resolve_threshold(specific_env: str, default: float = _DEFAULT_THRESHOLD) -> float:
    """Resolve a confidence threshold from environment variables.

    Priority order (first valid value wins):
        1. ``specific_env`` — per-tier override (e.g. ``GRIP_CONFIDENCE_HEURISTIC``)
        2. ``GRIP_CONFIDENCE_THRESHOLD`` — global override applied to every tier
        3. ``default`` — module fallback (0.70)

    Invalid values (non-float, or out of [0.0, 1.0]) are logged and skipped,
    falling through to the next priority. This mirrors HAL's D2 fail-loud
    pattern but degrades gracefully — a typo in env config relaxes
    confidence checks more than intended would be a worse failure mode
    than tightening them, but we err toward the operator's last-known
    intent rather than a silent revert.
    """
    for env_name in (specific_env, "GRIP_CONFIDENCE_THRESHOLD"):
        if not env_name:
            continue
        raw = os.environ.get(env_name)
        if raw is None:
            continue
        try:
            value = float(raw)
        except ValueError:
            logger.warning(
                "%s=%r is not a number; falling through to next threshold source",
                env_name, raw,
            )
            continue
        if not 0.0 <= value <= 1.0:
            logger.warning(
                "%s=%s is outside [0.0, 1.0]; falling through to next threshold source",
                env_name, value,
            )
            continue
        return value
    return default


# Tier 0 threshold: below this, escalate to Tier 1 (council).
# Override via GRIP_CONFIDENCE_HEURISTIC or GRIP_CONFIDENCE_THRESHOLD.
HEURISTIC_THRESHOLD = _resolve_threshold("GRIP_CONFIDENCE_HEURISTIC")

# Tier 1 threshold: below this, deny. Borderline (0.5-0.7) escalates to Tier 2.
# Override via GRIP_CONFIDENCE_COUNCIL_FAST or GRIP_CONFIDENCE_THRESHOLD.
COUNCIL_FAST_THRESHOLD = _resolve_threshold("GRIP_CONFIDENCE_COUNCIL_FAST")

# Tier 2 threshold: below this, deny. This is the final word.
# Override via GRIP_CONFIDENCE_COUNCIL_FULL or GRIP_CONFIDENCE_THRESHOLD.
COUNCIL_FULL_THRESHOLD = _resolve_threshold("GRIP_CONFIDENCE_COUNCIL_FULL")

# Tools that are always high-confidence (no measurement needed)
ALWAYS_CONFIDENT = frozenset({
    "AskUserQuestion", "TaskCreate", "TaskUpdate", "TaskGet", "TaskList",
    "TaskOutput", "TaskStop", "Read", "Glob", "Grep",
})

# Tools that warrant extra scrutiny (lower heuristic threshold)
HIGH_RISK_TOOLS = frozenset({
    "Bash", "Write", "Edit",
})

# Maximum transcript context to analyse (characters). The one governor of that
# length: hooks/context-gate-hook.py cuts the transcript with this constant too.
#
# No per-session budget is enforced on council calls. A SESSION_BUDGET_USD
# constant stood here and nothing read it (GRIP#8022), so it is gone: a number
# that changes nothing reads as a ceiling that is not there. HAL's own spend
# ceiling is GRIP_HAL_SESSION_BUDGET_USD (lib/hal/cost_tracker.py).
MAX_CONTEXT_CHARS = 2000


# ── Uncertainty Markers (Tier 0 Heuristic) ───────────────────────────────

# Hedging language that suggests the agent isn't sure
_HEDGING_PATTERNS = [
    re.compile(r"\bI think\b", re.IGNORECASE),
    re.compile(r"\bprobably\b", re.IGNORECASE),
    re.compile(r"\bmight\b", re.IGNORECASE),
    re.compile(r"\bperhaps\b", re.IGNORECASE),
    re.compile(r"\bnot sure\b", re.IGNORECASE),
    re.compile(r"\bI believe\b", re.IGNORECASE),
    re.compile(r"\bseems like\b", re.IGNORECASE),
    re.compile(r"\bshould work\b", re.IGNORECASE),
    re.compile(r"\blet me try\b", re.IGNORECASE),
    re.compile(r"\bif I'm not mistaken\b", re.IGNORECASE),
    re.compile(r"\bassuming\b", re.IGNORECASE),
    re.compile(r"\bguess\b", re.IGNORECASE),
]

# Retry indicators — same tool called multiple times suggests uncertainty
_RETRY_PATTERNS = [
    re.compile(r"trying again", re.IGNORECASE),
    re.compile(r"let me retry", re.IGNORECASE),
    re.compile(r"that didn't work", re.IGNORECASE),
    re.compile(r"failed.*trying", re.IGNORECASE),
]

# Question marks in assistant output suggest seeking clarification
_QUESTION_PATTERN = re.compile(r"\?")


# ── Result Types ─────────────────────────────────────────────────────────

@dataclass
class ConfidenceResult:
    """Result of confidence measurement."""
    confidence: float          # 0.0-1.0
    tier_reached: int          # 0, 1, or 2
    passed: bool               # True if confidence >= threshold
    reason: str = ""           # Human-readable explanation (plain language)
    signals: list[str] = field(default_factory=list)  # What triggered measurement
    cost_usd: float = 0.0     # Cost of council calls (Tier 1+)
    providers: list[str] = field(default_factory=list)  # Which providers scored
    duration_ms: float = 0.0   # Total measurement time


# ── Tier 0: Heuristic (Fast, Always Runs) ────────────────────────────────

def _heuristic_confidence(
    tool_name: str,
    tool_input: dict,
    transcript_context: str,
) -> tuple[float, list[str]]:
    """Fast pattern-based confidence estimate. Returns (score, signals).

    This is deliberately transparent: it counts uncertainty markers in
    the recent transcript and computes a simple ratio. No magic, no
    black box. The signals list explains exactly what was detected.
    """
    if tool_name in ALWAYS_CONFIDENT:
        return 1.0, []

    signals = []
    penalty = 0.0

    # Truncate context to limit processing
    context = transcript_context[-MAX_CONTEXT_CHARS:] if transcript_context else ""

    # Count hedging language
    hedge_count = 0
    for pattern in _HEDGING_PATTERNS:
        matches = pattern.findall(context)
        if matches:
            hedge_count += len(matches)
            signals.append(f"hedging: '{matches[0]}' ({len(matches)}x)")

    # Hedging penalty: each marker reduces confidence by 0.05, capped at 0.4
    if hedge_count > 0:
        penalty += min(hedge_count * 0.05, 0.40)

    # Retry indicators
    retry_count = 0
    for pattern in _RETRY_PATTERNS:
        matches = pattern.findall(context)
        if matches:
            retry_count += len(matches)
            signals.append(f"retry: '{matches[0]}'")

    # Retries are a strong signal — 0.1 per retry, capped at 0.3
    if retry_count > 0:
        penalty += min(retry_count * 0.10, 0.30)

    # Question marks in recent assistant output suggest uncertainty
    question_count = len(_QUESTION_PATTERN.findall(context))
    if question_count > 3:
        penalty += 0.10
        signals.append(f"questions: {question_count} in recent context")

    # High-risk tools get a baseline penalty
    if tool_name in HIGH_RISK_TOOLS:
        penalty += 0.05

    # Destructive command patterns in Bash
    if tool_name == "Bash":
        command = tool_input.get("command", "")
        if re.search(r"\brm\s+-rf\b", command):
            penalty += 0.15
            signals.append("destructive: rm -rf detected")
        if re.search(r"\bgit\s+push\b.*--force", command):
            penalty += 0.20
            signals.append("destructive: force push detected")

    confidence = max(0.0, 1.0 - penalty)
    return confidence, signals


# ── Tier 1: Fast Council (2 providers, ~2-5s) ────────────────────────────

def _tool_desc(tool_name: str, tool_input: dict) -> str:
    """The tool call as the outside council may see it: scrubbed whole, then cut.

    The council is an outside model, so the input passes the redaction shield
    before the 500-character cut; cutting first could split a name in two. Raises
    when the shield cannot load, which the callers treat as "council unavailable",
    so nothing is sent.
    """
    try:
        from review_redaction import WITHHELD, redact
    except ImportError:
        from lib.review_redaction import WITHHELD, redact
    scrubbed = redact(str(tool_input))
    if scrubbed == WITHHELD:
        raise RuntimeError("redaction shield failed; nothing sent to the council")
    return f"Tool: {tool_name}, Input: {scrubbed[:500]}"


def _council_fast(
    tool_name: str,
    tool_input: dict,
    signals: list[str],
) -> tuple[float, list[str], float]:
    """Two-provider quick evaluation. Returns (score, providers, cost).

    Uses HAL's external_score with mode="fast" — Gemini 2.5 Flash + GPT-OSS 120B.
    Only called when Tier 0 heuristic returns < HEURISTIC_THRESHOLD.
    """
    try:
        from moe.external_scorer import external_score

        tool_desc = _tool_desc(tool_name, tool_input)
        context = f"Uncertainty signals detected: {', '.join(signals)}"

        verdict = external_score(
            output=tool_desc,
            task=f"Evaluate whether this tool call should proceed. {context}",
            task_type="decision",
            dimension_weights={
                "safety": 0.40,
                "appropriateness": 0.30,
                "necessity": 0.30,
            },
            mode="fast",
        )

        providers = list(verdict.provider_scores.keys()) if hasattr(verdict, "provider_scores") else []
        confidence = verdict.composite_score / 100.0
        cost = getattr(verdict, "cost_usd", 0.0)
        return confidence, providers, cost

    except Exception as e:
        logger.warning("Tier 1 council failed, falling back to heuristic: %s", e)
        return 0.5, [], 0.0  # Graceful degradation: borderline


# ── Tier 2: Full Council (3 providers, 4 with the paid opt-in, ~10-30s) ──

def _council_full(
    tool_name: str,
    tool_input: dict,
    signals: list[str],
    tier1_score: float,
) -> tuple[float, list[str], float]:
    """Full multi-provider evaluation. Returns (score, providers, cost).

    Uses HAL's external_score with mode="standard" — 3 providers with
    KS divergence detection, and a 4th, the paid Haiku seat, only with
    GRIP_CLAUDE_ALLOW_PAID=1. Only called on Tier 1 borderline or disagreement.
    """
    try:
        from moe.external_scorer import external_score

        tool_desc = _tool_desc(tool_name, tool_input)
        context = (
            f"Uncertainty signals: {', '.join(signals)}. "
            f"Fast council scored {tier1_score:.0%}. "
            f"Escalated for full evaluation."
        )

        verdict = external_score(
            output=tool_desc,
            task=f"Evaluate whether this tool call should proceed. {context}",
            task_type="decision",
            dimension_weights={
                "safety": 0.40,
                "appropriateness": 0.30,
                "necessity": 0.30,
            },
            mode="standard",
        )

        providers = list(verdict.provider_scores.keys()) if hasattr(verdict, "provider_scores") else []
        confidence = verdict.composite_score / 100.0
        cost = getattr(verdict, "cost_usd", 0.0)

        # Log divergence for transparency
        if hasattr(verdict, "divergent_providers") and verdict.divergent_providers:
            logger.info(
                "Council divergence detected: %s (KS > 0.3)",
                verdict.divergent_providers,
            )

        return confidence, providers, cost

    except Exception as e:
        logger.warning("Tier 2 council failed, using Tier 1 score: %s", e)
        return tier1_score, [], 0.0


# ── Main Entry Point ─────────────────────────────────────────────────────

def measure_confidence(
    tool_name: str,
    tool_input: dict,
    transcript_context: str = "",
    skip_council: bool = False,
) -> ConfidenceResult:
    """Measure confidence in a tool call using 3-tier cascade.

    Tier 0 (heuristic) always runs. Tier 1 and Tier 2 only fire when
    uncertainty is detected. Set skip_council=True for heuristic-only mode
    (used by tests, and by the hook for a session outside a GRIP checkout).

    Returns a ConfidenceResult with:
        - confidence: 0.0-1.0
        - passed: True if above threshold
        - reason: plain-language explanation
        - signals: what triggered the measurement
        - tier_reached: how far up the cascade it went
    """
    start = time.monotonic()

    # ── Tier 0: Heuristic (always runs) ──
    heuristic_score, signals = _heuristic_confidence(
        tool_name, tool_input, transcript_context,
    )

    if heuristic_score >= HEURISTIC_THRESHOLD or skip_council:
        elapsed = (time.monotonic() - start) * 1000
        return ConfidenceResult(
            confidence=heuristic_score,
            tier_reached=0,
            # Derived from the score, never asserted. `passed=True` was a
            # literal here, so skip_council — the heuristic-only path, then
            # documented as the one taken "when budget is exhausted" —
            # reported PASS at any confidence,
            # including 0.0 on a destructive command. The gate switched
            # itself off exactly when the council it substitutes for was
            # unavailable, which is when it was most needed.
            passed=heuristic_score >= HEURISTIC_THRESHOLD,
            signals=signals,
            duration_ms=elapsed,
        )

    # ── Tier 1: Fast Council ──
    tier1_score, tier1_providers, tier1_cost = _council_fast(
        tool_name, tool_input, signals,
    )

    if tier1_score >= COUNCIL_FAST_THRESHOLD:
        elapsed = (time.monotonic() - start) * 1000
        return ConfidenceResult(
            confidence=tier1_score,
            tier_reached=1,
            passed=True,
            signals=signals,
            providers=tier1_providers,
            cost_usd=tier1_cost,
            duration_ms=elapsed,
        )

    # Borderline: escalate to Tier 2
    is_borderline = 0.50 <= tier1_score < COUNCIL_FAST_THRESHOLD

    if is_borderline:
        tier2_score, tier2_providers, tier2_cost = _council_full(
            tool_name, tool_input, signals, tier1_score,
        )
        elapsed = (time.monotonic() - start) * 1000
        all_providers = list(set(tier1_providers + tier2_providers))
        total_cost = tier1_cost + tier2_cost
        passed = tier2_score >= COUNCIL_FULL_THRESHOLD

        reason = ""
        if not passed:
            reason = (
                f"Confidence check: {tier2_score:.0%} (below {COUNCIL_FULL_THRESHOLD:.0%} threshold). "
                f"Signals: {', '.join(signals) if signals else 'none'}. "
                f"Checked by {len(all_providers)} independent models. "
                "Please confirm you want to proceed, or adjust the approach."
            )

        return ConfidenceResult(
            confidence=tier2_score,
            tier_reached=2,
            passed=passed,
            reason=reason,
            signals=signals,
            providers=all_providers,
            cost_usd=total_cost,
            duration_ms=elapsed,
        )

    # Below borderline on Tier 1: deny without Tier 2
    elapsed = (time.monotonic() - start) * 1000
    reason = (
        f"Confidence check: {tier1_score:.0%} (below {COUNCIL_FAST_THRESHOLD:.0%} threshold). "
        f"Signals: {', '.join(signals) if signals else 'none'}. "
        "This action was paused because the system detected uncertainty. "
        "Please confirm you want to proceed, or adjust the approach."
    )

    return ConfidenceResult(
        confidence=tier1_score,
        tier_reached=1,
        passed=False,
        reason=reason,
        signals=signals,
        providers=tier1_providers,
        cost_usd=tier1_cost,
        duration_ms=elapsed,
    )


# ── Hook Integration ─────────────────────────────────────────────────────

def as_hook_response(result: ConfidenceResult) -> dict | None:
    """Convert ConfidenceResult to a hook permissionDecision.

    Returns None if confidence is sufficient (allow).
    Returns a deny dict if confidence is too low.
    """
    if result.passed:
        return None

    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "userMessage": (
                f"[CONFIDENCE GATE] {result.reason}\n\n"
                f"Confidence: {result.confidence:.0%} | "
                f"Tier: {result.tier_reached} | "
                f"Providers: {', '.join(result.providers) if result.providers else 'heuristic only'}"
            ),
        }
    }
