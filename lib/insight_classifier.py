"""Insight classifier — categorises insights per Rule 20's classification matrix.

Categories (from efficiency-rules.md Rule 20):
1. conversation_only     — Contextual observation, ad-hoc reasoning, R0 critique
2. operational_discovery — Channel/DM/keychain/env var, endpoint URL, team detail
3. design_pattern        — Architectural lesson, pattern refinement
4. behavioral_rule       — "Always do X" / "Never do Y" recurring guidance
5. code_change           — Reveals a wrapper/test/refactor needed
6. documentation_gap     — Reveals a doc that doesn't exist

The classifier uses keyword heuristics + structural signals to assign a
category. When confidence is low, defaults to 'conversation_only' (safe —
auto-memory captures it, no propagation needed).

Design constraints:
- Pure function: no I/O, no state.
- Fast: regex-based, no LLM calls.
- Conservative: false negatives are cheaper than false positives.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import List, Tuple


class InsightCategory(Enum):
    """Rule 20 insight categories."""
    CONVERSATION_ONLY = "conversation_only"
    OPERATIONAL_DISCOVERY = "operational_discovery"
    DESIGN_PATTERN = "design_pattern"
    BEHAVIORAL_RULE = "behavioral_rule"
    CODE_CHANGE = "code_change"
    DOCUMENTATION_GAP = "documentation_gap"


@dataclass(frozen=True)
class ClassificationResult:
    """Result of classifying an insight."""
    category: InsightCategory
    confidence: float  # 0.0–1.0
    signals: List[str]  # Which signals matched
    requires_propagation: bool  # True if category needs action beyond auto-memory


# ---------------------------------------------------------------------------
# Signal patterns per category (compiled at module load)
# ---------------------------------------------------------------------------

# Operational discovery — concrete references to infrastructure
_OPERATIONAL_SIGNALS = [
    (r"channel[\s_-]?id|dm[\s_-]?id", "channel_id"),
    (r"(?-i:C[A-Z0-9]{8,})", "slack_channel_format"),
    (r"(?-i:D[A-Z0-9]{8,})", "slack_dm_format"),
    (r"endpoint\s*url|api\s*url|base\s*url", "endpoint_url"),
    (r"keychain|credential|secret\s*location", "credentials"),
    (r"env\s*var|environ|\.env\b", "env_var"),
    (r"team\s*member|@\w+>>|V>>|A>>|K>>", "team_member"),
    (r"port\s*\d+|localhost:\d+", "port_reference"),
]

# W0 (2026-05-05): "Strong" operational signals are concrete infrastructure
# references that constitute operational discoveries on their own. "Weak"
# signals (currently `team_member` only) reference people or vague evocations
# that are NOT operational evidence by themselves. The classifier requires
# at least one strong signal OR two distinct signals to route to
# OPERATIONAL_DISCOVERY. Without this gate, any V>>/A>>/K>>/@\\w+>>
# mention misroutes (V4 verified 28/50 live propagations on team_member alone;
# the test pack reproduces 70% FP rate). See
# tests/test_classifier_team_member_false_positive.py and
# plans/auto-insight-rsi-sprint-2026-05-05.md W0.
_STRONG_OPERATIONAL_SIGNALS = frozenset({
    "channel_id",
    "slack_channel_format",
    "slack_dm_format",
    "endpoint_url",
    "credentials",
    "env_var",
    "port_reference",
})

# Design pattern — architectural lessons
_DESIGN_PATTERN_SIGNALS = [
    (r"pattern\s*(is|worth|for|that)", "pattern_mention"),
    (r"architectural|architecture", "architecture"),
    (r"the\s+(right|correct|canonical)\s+(approach|pattern|way)", "canonical_pattern"),
    (r"lesson\s*(from|learned|is)", "lesson"),
    (r"shape\s*[A-C]", "sprint_shape"),
    (r"dispatch\s*pattern|fanout|chain\s*pattern", "dispatch_pattern"),
    (r"isolation\s*pattern|worktree\s*pattern", "isolation_pattern"),
    (r"contract\s*(is|says|requires)", "contract"),
    (r"invariant|postcondition|precondition", "formal_property"),
    (r"refactor\s*(to|into|pattern)", "refactor_pattern"),
    (r"composition\s*(contract|pattern)", "composition"),
]

# Behavioral rule — always/never guidance
_BEHAVIORAL_RULE_SIGNALS = [
    (r"\balways\s+(do|use|apply|check|run|call)", "always_directive"),
    (r"\bnever\s+(do|use|skip|ignore|bypass)", "never_directive"),
    (r"\bmust\s+(be|have|include|check)", "must_directive"),
    (r"\bshould\s+(always|never)", "should_directive"),
    (r"rule\s*\d+|Rule\s*\d+", "rule_reference"),
    (r"paramount|PARAMOUNT", "paramount_flag"),
    (r"no\s*exceptions?", "no_exceptions"),
    (r"banned\s*(pattern|output|phrase)", "banned_pattern"),
    (r"required\s*behaviour", "required_behaviour"),
]

# Code change — reveals work needed
_CODE_CHANGE_SIGNALS = [
    (r"needs?\s*(a\s+)?(test|wrapper|refactor|fix)", "needs_code"),
    (r"missing\s*(test|coverage|wrapper)", "missing_code"),
    (r"should\s+(add|write|create)\s+(a\s+)?(test|wrapper)", "should_add_code"),
    (r"pr\s*#\d+|PR\s*#\d+|pull\s*request", "pr_reference"),
    (r"bug\s*(in|at|here)|bug\s*fix", "bug_mention"),
    (r"todo|TODO|FIXME|fixme", "todo_marker"),
    (r"tech\s*debt|technical\s*debt", "tech_debt"),
]

# Documentation gap — reveals missing docs
_DOC_GAP_SIGNALS = [
    (r"\b(?:documentation|docs?)\s+(?:doesn't|does\s*not)\s+exist", "missing_doc"),
    (r"undocumented|not\s*documented", "undocumented"),
    (r"should\s*(document|add\s*to\s*docs)", "should_document"),
    (r"readme|README", "readme_mention"),
    (r"no\s*(docs?|documentation)\s*(for|about)", "no_docs"),
    (r"skill\s*(file|md)\s*(missing|needs)", "skill_file_gap"),
]

# Conversation-only signals (negative signals — things that DON'T need propagation)
_CONVERSATION_ONLY_SIGNALS = [
    (r"in\s*this\s*(specific\s*)?case", "specific_case"),
    (r"r0\s*critique|R0\s*critique", "r0_critique"),
    (r"contextual|ad[\s-]?hoc", "contextual"),
    (r"observation\s*(about|on|here)", "observation"),
    (r"interesting\s*(that|how|to\s*note)", "interesting_note"),
]


def _compile_patterns(
    patterns: List[Tuple[str, str]]
) -> List[Tuple[re.Pattern, str]]:
    """Compile a list of (regex, signal_name) tuples."""
    return [(re.compile(p, re.IGNORECASE), name) for p, name in patterns]


# Compiled patterns (module-level, done once)
_OPERATIONAL_COMPILED = _compile_patterns(_OPERATIONAL_SIGNALS)
_DESIGN_PATTERN_COMPILED = _compile_patterns(_DESIGN_PATTERN_SIGNALS)
_BEHAVIORAL_RULE_COMPILED = _compile_patterns(_BEHAVIORAL_RULE_SIGNALS)
_CODE_CHANGE_COMPILED = _compile_patterns(_CODE_CHANGE_SIGNALS)
_DOC_GAP_COMPILED = _compile_patterns(_DOC_GAP_SIGNALS)
_CONVERSATION_ONLY_COMPILED = _compile_patterns(_CONVERSATION_ONLY_SIGNALS)


def _count_signals(
    text: str, patterns: List[Tuple[re.Pattern, str]]
) -> Tuple[int, List[str]]:
    """Count how many patterns match and return their names."""
    matched = [name for pattern, name in patterns if pattern.search(text)]
    return len(matched), matched


# Category-weight table. Doc-gap > design-pattern because explicit "should
# document" / "undocumented" signals carry stronger intent than incidental
# "pattern" mentions in the same sentence (e.g. "should document this pattern"
# routes to doc gap, not pattern). conversation_only is penalised so that any
# substantive category beats it.
_CATEGORY_WEIGHTS = {
    InsightCategory.OPERATIONAL_DISCOVERY: 1.5,  # concrete infra refs
    InsightCategory.DOCUMENTATION_GAP: 1.4,
    InsightCategory.BEHAVIORAL_RULE: 1.3,
    InsightCategory.DESIGN_PATTERN: 1.2,
    InsightCategory.CODE_CHANGE: 1.0,
    InsightCategory.CONVERSATION_ONLY: 0.8,
}

_CATEGORY_PATTERNS = {
    InsightCategory.OPERATIONAL_DISCOVERY: _OPERATIONAL_COMPILED,
    InsightCategory.DOCUMENTATION_GAP: _DOC_GAP_COMPILED,
    InsightCategory.BEHAVIORAL_RULE: _BEHAVIORAL_RULE_COMPILED,
    InsightCategory.DESIGN_PATTERN: _DESIGN_PATTERN_COMPILED,
    InsightCategory.CODE_CHANGE: _CODE_CHANGE_COMPILED,
    InsightCategory.CONVERSATION_ONLY: _CONVERSATION_ONLY_COMPILED,
}


def _collect_category_signals(content: str) -> dict[InsightCategory, List[str]]:
    """Run signal detection for all categories. Returns category → matched signals."""
    return {
        cat: _count_signals(content, patterns)[1]
        for cat, patterns in _CATEGORY_PATTERNS.items()
    }


def _demote_weak_operational(signals: dict[InsightCategory, List[str]]) -> None:
    """W0 fix (2026-05-05): operational routing requires either a STRONG
    signal (concrete infra reference) or multiple distinct signals.

    Without this gate, signature mentions (V>>/A>>/K>>/@\\w+>>) alone
    match the team_member regex, score 1.5 and beat every other category —
    the live 28/50 misroute pattern V4 caught and the 2026-04-30 manual
    cleanup of rules/operational-context.md was patching. Mutates in place.
    """
    op_signals = signals[InsightCategory.OPERATIONAL_DISCOVERY]
    has_strong = any(s in _STRONG_OPERATIONAL_SIGNALS for s in op_signals)
    if not has_strong and len(op_signals) < 2:
        signals[InsightCategory.OPERATIONAL_DISCOVERY] = []


def _score_categories(
    signals: dict[InsightCategory, List[str]],
) -> dict[InsightCategory, float]:
    """Apply per-category weights to signal counts."""
    return {cat: len(sigs) * _CATEGORY_WEIGHTS[cat] for cat, sigs in signals.items()}


def _default_conversation(co_signals: List[str]) -> ClassificationResult:
    """Fallback when no substantive signal hits."""
    return ClassificationResult(
        category=InsightCategory.CONVERSATION_ONLY,
        confidence=0.9 if co_signals else 0.5,
        signals=co_signals if co_signals else ["no_strong_signals"],
        requires_propagation=False,
    )


def classify(content: str) -> ClassificationResult:
    """Classify an insight into a Rule 20 category.

    Returns ClassificationResult with category, confidence, and matched signals.
    Empty/whitespace input -> CONVERSATION_ONLY at confidence 1.0.
    """
    if not content or not content.strip():
        return ClassificationResult(
            category=InsightCategory.CONVERSATION_ONLY,
            confidence=1.0,
            signals=["empty_content"],
            requires_propagation=False,
        )

    signals = _collect_category_signals(content)
    _demote_weak_operational(signals)
    scores = _score_categories(signals)

    best_cat = max(scores, key=lambda k: scores[k])
    best_score = scores[best_cat]
    substantive_count = sum(
        len(s) for c, s in signals.items() if c != InsightCategory.CONVERSATION_ONLY
    )

    if substantive_count == 0 or best_score < 1.0:
        return _default_conversation(signals[InsightCategory.CONVERSATION_ONLY])

    return ClassificationResult(
        category=best_cat,
        confidence=min(0.95, 0.5 + (best_score / 10)),
        signals=signals[best_cat],
        requires_propagation=best_cat != InsightCategory.CONVERSATION_ONLY,
    )


def classify_batch(contents: List[str]) -> List[ClassificationResult]:
    """Classify multiple insights."""
    return [classify(c) for c in contents]
