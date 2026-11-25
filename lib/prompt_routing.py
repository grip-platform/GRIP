"""Prompt Routing — Auto-assess user prompts and recommend execution patterns.

Composition layer over existing GRIP scoring libraries. Does NOT reinvent
the scoring — reuses:

  - prompt_router.classify_prompt  — BAR 5-signal heuristic + semantic
  - auto_mode_selector.select_modes (lazy, opt-in)  — LLM-inferred modes
  - confidence_gate.measure_confidence (lazy, opt-in)  — 3-tier cascade
  - hal.sentinel state file              — provider availability gate
  - hal.cost_tracker.calculate_cost      — per-model USD pricing

Public API:

    from prompt_routing import assess, is_exempt, parse_implied_pattern

    assessment = assess("fix the typo on line 42", session_context={})
    if not assessment.exempt:
        print(assessment.recommended_pattern)  # "Direct" | "Focused" | ...
        print(assessment.mismatch_tiers)       # 0 = match, 2+ = significant
        print(assessment.cost_aware_pattern)   # cost-driven downgrade or None

Design principles:
  - Fail-safe over fail-secure: any exception → assume exempt, do not block
  - Lazy imports: keep cold-start cost low for the hook fast path
  - Pattern matching first, LLM only when unavoidable
  - No state, no I/O beyond reading calibrated weights and HAL Sentinel state
"""

import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


# ============================================================================
# Pattern Taxonomy
# ============================================================================
#
# Four execution patterns, aligned with skills/execution-router/SKILL.md.
# Mapping to BAR power levels (prompt_router.py):
#
#   Direct    ← thread           (score 0-5)
#   Focused   ← fork              (score 6-10)
#   Sprint    ← cluster, pipeline (score 11-13)
#   FullMesh  ← mesh              (score 14-15)

PATTERNS = ("Direct", "Focused", "Sprint", "FullMesh")
_PATTERN_ORDER = {p: i for i, p in enumerate(PATTERNS)}

_BAR_TO_PATTERN = {
    "thread": "Direct",
    "fork": "Focused",
    "cluster": "Sprint",
    "pipeline": "Sprint",
    "mesh": "FullMesh",
}


# ============================================================================
# Exemptions (Recall First, Rule 0)
# ============================================================================
#
# Retrieval discipline steps aside for six categories. These are the same
# exemptions that grip-first-retrieval-hook.py uses for tier 0 retrieval.

_CONVERSATIONAL_PREFIXES = (
    "thanks", "thank you", "ty", "cheers", "great", "perfect", "nice",
    "rl++", "ok", "okay", "yes", "no", "sure", "cool", "awesome",
    "hello", "hi ", "hey",
)

_DIRECT_COMMAND_PATTERNS = (
    r"^(commit|push|merge|pull|rebase)\b",        # Git verbs
    r"^(run|execute|show)\s+\S+",                 # Run/exec directives
    r"^(edit|modify|change)\s+line\s+\d+",        # Surgical edits
    r"^(rename|move|copy)\s+\S+\s+to\s+\S+",      # File ops
)

# Self-contained slash commands — no task content needed, always exempt.
# These are meta-commands that operate on session state, not user intent.
_SELF_CONTAINED_SLASH_COMMANDS = frozenset({
    "/save", "/recall", "/status", "/list", "/help", "/mode",
    "/commit", "/push", "/pull", "/create-pr", "/shipit",
    "/onboard", "/activate-grip", "/grip-doctor", "/grip-config",
    "/save-session", "/resume", "/refresh-context", "/remind-yourself",
    "/prune-branches", "/setup-ci", "/setup-launchd", "/sync",
    "/catch-me-up", "/audit-efficiency", "/markdown-lint",
    "/coffee", "/insights",
})

_PURE_CREATION_HINTS = (
    "write a new", "create a new file", "draft a", "compose a",
    "generate a", "make me a",
)

_WEB_SEARCH_REQUESTS = (
    "google", "search the web", "look up online", "find on the internet",
    "fetch https://", "fetch http://", "web search",
)


def _split_slash_command(text: str) -> tuple[Optional[str], str]:
    """If text starts with a slash command, return (command, content_after).

    Otherwise (None, text). Allows routing on the content of a heavy
    slash command like `/rsi-optimal "add a comment"` so downgrade
    detection still fires even though the prefix is a slash command.
    """
    if not text.startswith("/"):
        return None, text
    parts = text.split(None, 1)
    command = parts[0].lower()
    content = parts[1] if len(parts) > 1 else ""
    return command, content


def _check_slash_exemption(text: str) -> tuple[Optional[tuple[bool, str]], str]:
    """Check slash-command exemption. Returns ((exempt, reason), routable_text).

    If exempt → first element is (True, reason); caller returns it immediately.
    If not exempt but is a slash command → first element is None and
    routable_text is the content after the command.
    Otherwise → (None, text).
    """
    command, content = _split_slash_command(text)
    if command is None:
        return None, text
    if command in _SELF_CONTAINED_SLASH_COMMANDS:
        return (True, "self_contained_command"), text
    if not content:
        return (True, "bare_slash_command"), text
    return None, content


def _check_keyword_exemptions(text: str) -> Optional[str]:
    """Check conversational, direct-command, creation, search, in-context."""
    lower = text.lower()

    if any(lower.startswith(p) for p in _CONVERSATIONAL_PREFIXES):
        if len(text.split()) <= 5:
            return "conversational"

    for pattern in _DIRECT_COMMAND_PATTERNS:
        if re.match(pattern, text, re.IGNORECASE):
            return "direct_command"

    if any(hint in lower for hint in _PURE_CREATION_HINTS):
        return "pure_creation"

    if any(hint in lower for hint in _WEB_SEARCH_REQUESTS):
        return "external_search"

    if text.count("```") >= 2 and "?" not in text:
        return "in_context_data"

    return None


def is_exempt(prompt: str) -> tuple[bool, Optional[str]]:
    """Return (exempt, reason).

    True when the prompt matches one of the GRIP-First exemptions. Slash
    commands are handled with nuance:
      - Self-contained meta-commands (`/save`, `/recall`) → exempt
      - Bare heavy commands (no content) → exempt
      - Heavy commands WITH content (`/rsi-optimal "..."`) → route on content
    """
    text = prompt.strip()
    if not text:
        return True, "empty"

    slash_result, routable_text = _check_slash_exemption(text)
    if slash_result is not None:
        return slash_result

    reason = _check_keyword_exemptions(routable_text)
    if reason is not None:
        return True, reason

    return False, None


# ============================================================================
# Implied pattern parsing
# ============================================================================
#
# Detect the pattern the user appears to be asking for from their phrasing.
# Used for mismatch detection — if user says "quick" but scorer returns
# Sprint, that's a two-tier mismatch worth confirming.

_PATTERN_PHRASES = {
    "Direct": (
        "quick", "simple", "small", "tiny", "minor", "tweak",
        "fix the typo", "rename", "just",
    ),
    "Focused": (
        "feature", "add", "implement", "integrate", "refactor",
        "wire up", "hook up",
    ),
    "Sprint": (
        "sprint", "rsi", "wave", "session", "converge",
        "/converge", "/rsi", "/broly",
    ),
    "FullMesh": (
        "/rsi-optimal", "/ultrado", "full mesh", "full-mesh", "council",
        "research council", "multi-model", "falsification",
        "full codebase", "entire project", "architecture review",
    ),
}

# Pre-compiled word-boundary patterns per tier. Slash-command phrases are
# matched as literals (prefix check); natural-language phrases use \b guards
# so "add" does not match inside "padding", "rsi" does not match inside "parsing".
_PATTERN_REGEXES: dict[str, re.Pattern] = {
    pattern: re.compile(
        "|".join(
            phrase if phrase.startswith("/") else r"\b" + re.escape(phrase) + r"\b"
            for phrase in phrases
        ),
        re.IGNORECASE,
    )
    for pattern, phrases in _PATTERN_PHRASES.items()
}


def parse_implied_pattern(prompt: str) -> Optional[str]:
    """Parse the user's implied execution pattern from their phrasing.

    Returns None if no clear signal. Does NOT guess — absence of signal
    means the user has no strong preference and the scorer's output wins.

    Checks both the raw prompt (for natural-language phrases) and the
    slash command prefix (for pattern-carrying commands like `/rsi-optimal`).
    """
    # Slash-command prefix carries strong pattern signal
    command, _ = _split_slash_command(prompt)
    if command:
        for pattern in reversed(PATTERNS):
            if any(p.startswith("/") and p == command for p in _PATTERN_PHRASES[pattern]):
                return pattern

    # Natural-language phrase signal (Full Mesh first — most specific)
    # Word-boundary regex prevents substring false positives (e.g. "add" in "padding")
    for pattern in reversed(PATTERNS):
        if _PATTERN_REGEXES[pattern].search(prompt):
            return pattern
    return None


# ============================================================================
# Assessment
# ============================================================================


@dataclass
class RoutingAssessment:
    """Result of assessing a user prompt for optimal execution pattern."""

    exempt: bool = False
    exemption_reason: Optional[str] = None

    score: int = 0                       # 0-15 composite complexity (scaled)
    power_level: str = "thread"          # BAR: thread/fork/cluster/pipeline/mesh
    confidence: float = 0.0              # 0-1

    recommended_pattern: str = "Direct"  # Direct/Focused/Sprint/FullMesh
    user_implied_pattern: Optional[str] = None
    mismatch_tiers: int = 0              # |recommended - implied|, 0 if unknown

    rationale: str = ""
    signals: dict = field(default_factory=dict)

    # Cost-awareness (HAL Sentinel + cost_tracker integration)
    cost_aware_pattern: Optional[str] = None  # Suggested cheaper pattern, or None
    cost_aware_reason: Optional[str] = None   # Human-readable downgrade rationale
    provider_tier: Optional[str] = None       # paid_premium/paid_standard/free/local


# ============================================================================
# Cost-awareness (HAL Sentinel + cost_tracker integration)
# ============================================================================
#
# Consults the HAL Sentinel state file and the cost_tracker pricing table
# to recommend a cheaper pattern when the session is under budget pressure
# or the primary provider is near its utilisation limit.
#
# All HAL access is via file-read (no class instantiation) so prompt_routing
# stays a clean composition layer with no live HAL dependency.

# Default sentinel state path (mirrors lib/hal/sentinel.py _STATE_PATH)
_SENTINEL_STATE_FILE = Path(
    os.environ.get("GRIP_HAL_HOME", str(Path.home() / ".claude"))
) / "state" / "hal" / "limits.json"

# Default budget threshold in USD per session
_DEFAULT_BUDGET_USD = 5.0

# Sentinel utilisation threshold for cost-driven downgrade (mirror Sentinel _WARNING)
_DOWNGRADE_UTIL_THRESHOLD = 0.70

# Cost-driven downgrade audit log
_COST_DOWNGRADE_LOG = Path(
    os.environ.get("GRIP_STATE_DIR", str(Path.home() / ".claude" / "state"))
) / "routing" / "cost-downgrades.jsonl"

# Provider tier assignment for the four patterns. Sprint/FullMesh imply
# paid_premium (Opus-tier); Focused implies paid_standard (Sonnet-tier);
# Direct implies free or local.
_PATTERN_TO_TIER = {
    "Direct": "free",
    "Focused": "paid_standard",
    "Sprint": "paid_premium",
    "FullMesh": "paid_premium",
}

# Phrases that mark a task as ineligible for cost-driven downgrade.
# Hypothesis-driven and architecture-wide work is user-owned; never downgrade.
_DOWNGRADE_BLOCKERS = (
    "hypothesis", "falsification", "falsifier", "h001", "h0",
    "architecture review", "review the architecture", "full codebase",
    "entire project", "whole codebase", "review the entire",
)


def _read_sentinel_utilisation(provider_name: str = "anthropic") -> Optional[float]:
    """Read provider utilisation from HAL Sentinel state file.

    Returns None when the file is missing, unreadable, or the provider has
    no recorded state. Fail-safe: never raises.
    """
    try:
        if not _SENTINEL_STATE_FILE.exists():
            return None
        with open(_SENTINEL_STATE_FILE, "r") as f:
            data = json.load(f)
        state = data.get(provider_name)
        if not state:
            return None
        util = state.get("utilisation")
        return float(util) if util is not None else None
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _is_downgrade_eligible(prompt: str) -> bool:
    """Check if the prompt's task is eligible for cost-driven downgrade.

    Hypothesis-driven, architecture-review, and codebase-wide tasks are
    user-owned and never downgraded — even under budget pressure.
    """
    lower = prompt.lower()
    return not any(blocker in lower for blocker in _DOWNGRADE_BLOCKERS)


def _budget_breached(session_cost_usd: float) -> bool:
    """Check if session cost has exceeded the configured budget."""
    try:
        budget = float(os.environ.get("GRIP_COST_BUDGET_USD", _DEFAULT_BUDGET_USD))
    except ValueError:
        budget = _DEFAULT_BUDGET_USD
    return session_cost_usd >= budget


def _downgrade_reason(util: Optional[float], cost_usd: float, breached: bool) -> str:
    """Build human-readable rationale for a cost-driven downgrade."""
    parts = []
    if util is not None and util >= _DOWNGRADE_UTIL_THRESHOLD:
        parts.append(f"anthropic utilisation {util:.2f} >= {_DOWNGRADE_UTIL_THRESHOLD}")
    if breached:
        parts.append(f"session cost ${cost_usd:.2f} >= budget threshold")
    return "; ".join(parts) if parts else ""


def _log_cost_downgrade(
    original: str,
    downgraded: str,
    reason: str,
    util: Optional[float],
    cost_usd: float,
    log_path: Optional[str] = None,
) -> None:
    """Append one JSONL audit record per cost-driven downgrade. Never raises."""
    try:
        path = Path(log_path) if log_path else _COST_DOWNGRADE_LOG
        path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "original_pattern": original,
            "downgraded_pattern": downgraded,
            "reason": reason,
            "sentinel_util": util,
            "session_cost_usd": cost_usd,
        }
        with open(path, "a") as f:
            f.write(json.dumps(record) + "\n")
    except OSError:
        pass


def _apply_cost_awareness(
    assessment: "RoutingAssessment",
    prompt: str,
    session_cost_usd: float = 0.0,
) -> "RoutingAssessment":
    """Augment assessment with cost-awareness fields. Pure (no in-place mutation).

    Downgrade rule (4-condition AND):
      1. BAR pattern is Sprint or FullMesh
      2. Sentinel utilisation >= 0.70 OR session cost >= budget
      3. Task is downgrade-eligible (not hypothesis/architecture-tier)
      4. A cheaper provider tier exists (Focused for Sprint, Sprint for FullMesh)
    """
    assessment.provider_tier = _PATTERN_TO_TIER.get(assessment.recommended_pattern)

    if assessment.recommended_pattern not in ("Sprint", "FullMesh"):
        return assessment

    util = _read_sentinel_utilisation()
    util_breached = util is not None and util >= _DOWNGRADE_UTIL_THRESHOLD
    cost_breached = _budget_breached(session_cost_usd)
    if not (util_breached or cost_breached):
        return assessment

    if not _is_downgrade_eligible(prompt):
        return assessment

    # Downgrade target: Sprint -> Focused, FullMesh -> Sprint
    target = "Focused" if assessment.recommended_pattern == "Sprint" else "Sprint"
    reason = _downgrade_reason(util, session_cost_usd, cost_breached)

    assessment.cost_aware_pattern = target
    assessment.cost_aware_reason = reason
    _log_cost_downgrade(
        assessment.recommended_pattern, target, reason, util, session_cost_usd
    )
    return assessment


def _bar_to_pattern(power_level: str) -> str:
    return _BAR_TO_PATTERN.get(power_level, "Direct")


def _composite_score(power_level: str, confidence: float) -> int:
    """Map BAR (power_level, confidence) to a 0-15 composite complexity.

    The BAR classifier outputs a discrete level and a confidence. We want
    a single integer that aligns with the 5-dimension taxonomy described
    in skills/execution-router/SKILL.md (novelty+scope+duration+uncertainty+risk,
    each 1-3, max 15).

    Mapping:
        thread,   conf ≥ 0.5 → 3
        thread,   conf < 0.5 → 5 (ambiguous — lean toward more work)
        fork,     any         → 7 + (conf ≥ 0.7 ? 1 : 0)
        cluster,  any         → 10 + (conf ≥ 0.7 ? 1 : 0)
        pipeline, any         → 12 + (conf ≥ 0.7 ? 1 : 0)
        mesh,     any         → 14 + (conf ≥ 0.7 ? 1 : 0)
    """
    base = {
        "thread": 3 if confidence >= 0.5 else 5,
        "fork": 7,
        "cluster": 10,
        "pipeline": 12,
        "mesh": 14,
    }.get(power_level, 3)
    # High-confidence bumps (except thread — high confidence confirms simplicity)
    if power_level != "thread" and confidence >= 0.7:
        base += 1
    return min(base, 15)


def _mismatch_tiers(recommended: str, implied: Optional[str]) -> int:
    """Count tier distance between recommended and implied patterns."""
    if implied is None:
        return 0
    return abs(_PATTERN_ORDER[recommended] - _PATTERN_ORDER[implied])


def _classify_via_bar(prompt: str, session_context: Optional[dict]):
    """Lazy-import and call the BAR classifier. Returns (decision, error)."""
    try:
        import sys
        from pathlib import Path
        _lib_dir = str(Path(__file__).resolve().parent)
        if _lib_dir not in sys.path:
            sys.path.insert(0, _lib_dir)

        from prompt_router import classify_prompt  # type: ignore
        return classify_prompt(prompt, context=session_context), None
    except Exception as exc:
        return None, exc


def _build_rationale(decision, score: int, implied: Optional[str], mismatch: int) -> str:
    """Assemble the human-readable rationale string for a RoutingAssessment."""
    parts = [
        f"BAR={decision.power_level}",
        f"score={score}/15",
        f"confidence={decision.confidence:.0%}",
    ]
    if implied:
        parts.append(f"implied={implied}")
    if mismatch >= 2:
        parts.append(f"MISMATCH={mismatch}")
    return " ".join(parts)


def assess(
    prompt: str,
    session_context: Optional[dict] = None,
    session_cost_usd: float = 0.0,
) -> RoutingAssessment:
    """Assess a prompt and return a RoutingAssessment.

    Fail-safe: any exception returns a default "exempt" assessment so the
    agent proceeds with the user's original framing. Never raises.

    `session_cost_usd` is consulted for cost-aware downgrade. Defaults to 0
    so existing callers continue to work without code change.
    """
    exempt, reason = is_exempt(prompt)
    if exempt:
        return RoutingAssessment(
            exempt=True,
            exemption_reason=reason,
            rationale=f"Exempt: {reason}",
        )

    decision, error = _classify_via_bar(prompt, session_context)
    if error is not None:
        return RoutingAssessment(
            exempt=True,
            exemption_reason="classifier_unavailable",
            rationale=f"Classifier failed: {error}",
            signals={"error": str(error)},
        )

    recommended = _bar_to_pattern(decision.power_level)
    implied = parse_implied_pattern(prompt)
    mismatch = _mismatch_tiers(recommended, implied)
    score = _composite_score(decision.power_level, decision.confidence)

    assessment = RoutingAssessment(
        exempt=False,
        score=score,
        power_level=decision.power_level,
        confidence=decision.confidence,
        recommended_pattern=recommended,
        user_implied_pattern=implied,
        mismatch_tiers=mismatch,
        rationale=_build_rationale(decision, score, implied, mismatch),
        signals=dict(decision.signals) if decision.signals else {},
    )
    final = _apply_cost_awareness(assessment, prompt, session_cost_usd)

    # Pure observability — log the dispatch decision (issue #2558).
    # Never blocks routing; final return value is unchanged.
    try:
        from dispatch_logger import log_dispatch  # local import: lazy + cycle-safe
        log_dispatch(prompt, final)
    except Exception:
        pass

    return final


# ============================================================================
# CLI entrypoint for debugging + tests
# ============================================================================


def _format_assessment(a: RoutingAssessment) -> str:
    if a.exempt:
        return f"EXEMPT ({a.exemption_reason}): {a.rationale}"
    lines = [
        f"Pattern:   {a.recommended_pattern}",
        f"Score:     {a.score}/15  (BAR {a.power_level}, conf {a.confidence:.0%})",
    ]
    if a.user_implied_pattern:
        lines.append(f"Implied:   {a.user_implied_pattern}  (mismatch tiers: {a.mismatch_tiers})")
    if a.provider_tier:
        lines.append(f"Tier:      {a.provider_tier}")
    if a.cost_aware_pattern:
        lines.append(f"Cost-aware: downgrade to {a.cost_aware_pattern}  ({a.cost_aware_reason})")
    lines.append(f"Rationale: {a.rationale}")
    return "\n".join(lines)


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python3 prompt_routing.py <prompt>")
        sys.exit(1)
    prompt = " ".join(sys.argv[1:])
    assessment = assess(prompt)
    print(_format_assessment(assessment))
