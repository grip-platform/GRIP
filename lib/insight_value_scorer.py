"""Insight value scorer — decides whether a harvested insight is worth persisting.

Heuristic classifier that computes:
- A float value score in [0, 1]
- A memory type (reference | feedback | project | user)
- A suggested filename slug
- A suggested one-line MEMORY.md hook (≤ 150 chars)
- A dedup fingerprint (SHA1 of normalised content)

Conservative by design. Threshold defaults ensure low false-positive rate:
MEMORY.md pollution is a high-cost mistake. Better to miss an insight than
to save one that V>> later has to prune.

Relationship to the auto-memory protocol in CLAUDE.md:
- Reference memories: "when X, use Y at path Z"
- Feedback memories: approach corrections or confirmations
- Project memories: in-flight initiatives with Why/How-to-apply
- User memories: facts about the operator

Assistant-emitted ★ Insight blocks are overwhelmingly REFERENCE in nature
(technical discoveries that future-me should know). FEEDBACK and USER
insights almost always come from user messages, not assistant output —
those are handled by learning-hook.py on UserPromptSubmit.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path


# ---------------------------------------------------------------------------
# Scoring signals (keyword heuristics, case-insensitive)
# ---------------------------------------------------------------------------

# Positive signals — regexes, word-root-tolerant (confirm/confirms/confirmed).
_TOKEN_ECONOMY_SIGNALS = (
    r"wast\w*\s+tokens?", r"tokens?\s+(cost|waste|sav)",
    r"opus\s*4[.]\d", r"explore\s+agent", r"expensive",
    r"88k", r"440x", r"burn\w*\s+cache",
)
_MEMORY_GAP_SIGNALS = (
    r"without this memory", r"future[\s-](session|me|absorb)",
    r"next session", r"will\s+forget", r"won'?t\s+know",
    r"wouldn'?t\s+know", r"persist", r"remember this",
)
_REFERENCE_PATTERN_SIGNALS = (
    r"fallback", r"\bfails?\b.*\b(retry|use|fall|try)\b",
    r"use\s+.+?\s+instead", r"at\s+path", r"tool\s+at",
    r"close[sd]?\s+the\s+gap", r"the\s+\w*\s*fix\s+is", r"clean\s+(fallback|path|retry)",
    r"closes? without", r"closes the gap",
    r"pattern\s+worth", r"right\s+approach", r"root\s+cause",
    r"should\s+(never|always)\s+be",
)
_SHELL_PATH_SIGNALS = (
    r"(/lib/|/scripts/|/hooks/|~/.claude/)\w",
    r"\b\w+[-_]?\w+\.(sh|py)\s+--\w+",
    r"--\w+\s+\w+",
    r"`\w[\w/_-]+\.(py|ts|sh|json|md)`",  # backtick-wrapped file references
    r"line\s+\d+",                         # "line 38" style file references
)
_NONOBVIOUS_SIGNALS = (
    r"non-obvious", r"hidden", r"invariant", r"gotcha", r"surprise",
    r"counterintuitive", r"subtle", r"easy to miss",
    r"anti.?pattern", r"silent(ly)?\s+(exit|fail|bypass)",
    r"invisible\s+fail", r"rule\s+\d+", r"paramount",
)
_EVIDENCE_SIGNALS = (
    r"\bevidence", r"confirm\w*", r"falsif\w*", r"proof", r"verif\w*",
    r"measure\w*", r"observe\w*", r"transient",
)
# R0 — Critical Thinking Gate: reward explicit falsifiability and scope-limiting.
# Insights that state what would disprove them are more valuable than universal claims.
_FALSIFIABILITY_SIGNALS = (
    r"would be wrong if", r"fails\s+(when|if)", r"except\s+(when|in|for)",
    r"\bunless\b", r"in\s+this\s+(case|codebase|context)",
    r"confirmed\s+by", r"verified\s+via",
    r"test\s+(shows|confirms|verifies)", r"observed\s+(that|in|when)",
    r"this\s+would\s+(not|fail)", r"scope\s+qualifier", r"falsif\w*\s+condition",
)

# Negative signals — things that DON'T warrant a persistent memory.
_BOILERPLATE_PREFIXES = (
    "the code", "this function", "you can see", "here we",
    "as you can see", "let me", "i'll", "i will", "i'm going to",
)
_EPHEMERAL_SIGNALS = (
    "just ran", "just now", "this turn", "in this session only",
    "for this call", "one-off",
)
# R0 — overgeneralization markers: "obviously" / "clearly" signal post-hoc
# rationalization without evidence. Penalise but don't block (threshold gate handles it).
_OVERGENERALIZATION_SIGNALS = (
    r"\bobviously\b", r"\bclearly\b", r"\balways\s+\w+\b(?!.*unless)",
    r"\bnever\s+\w+\b(?!.*unless)", r"this\s+is\s+always", r"you\s+should\s+always",
)


def _count_signals(text: str, signals: tuple[str, ...]) -> int:
    """Count how many signal phrases match (as regex)."""
    text = text.lower()
    count = 0
    for sig in signals:
        if re.search(sig, text):
            count += 1
    return count


# ---------------------------------------------------------------------------
# Classifier (memory type)
# ---------------------------------------------------------------------------


def _classify(text: str) -> str:
    """Return one of: reference, feedback, project, user."""
    t = text.lower()

    # Feedback: explicit correction framing
    if any(p in t for p in ("don't ", "do not ", "never ", "stop ", "incorrect")):
        if any(p in t for p in ("because", "reason", "prior incident")):
            return "feedback"

    # Project: current-initiative language
    if any(p in t for p in ("sprint", "pr #", "merge freeze", "deadline",
                             "in-flight", "currently")):
        return "project"

    # User: about the operator
    if any(p in t for p in ("the user is", "v>> is", "laurie is",
                             "the operator", "the founder")):
        return "user"

    # Default: reference (pointer-style insight)
    return "reference"


# ---------------------------------------------------------------------------
# Slug + hook generation
# ---------------------------------------------------------------------------

_SLUG_STOPWORDS = {
    "the", "a", "an", "is", "to", "of", "and", "or", "but", "in", "on",
    "at", "for", "with", "by", "from", "it", "this", "that", "these",
    "those", "we", "be", "as", "are",
}


def _slug(text: str, max_words: int = 5) -> str:
    """Generate a filename slug from insight content."""
    words = re.findall(r"[a-z0-9]+", text.lower())
    kept = [w for w in words if w not in _SLUG_STOPWORDS and len(w) > 2]
    return "_".join(kept[:max_words]) or "insight"


def _hook(text: str, max_chars: int = 140) -> str:
    """Generate a one-line MEMORY.md hook (≤ 150 chars incl. suffix)."""
    collapsed = re.sub(r"\s+", " ", text.strip())
    if len(collapsed) <= max_chars:
        return collapsed
    cut = collapsed[:max_chars]
    if " " in cut:
        cut = cut.rsplit(" ", 1)[0]
    return cut + "…"


# ---------------------------------------------------------------------------
# Fingerprint (for dedup)
# ---------------------------------------------------------------------------


def fingerprint(text: str) -> str:
    """SHA1 of normalised content. Used to dedup against existing memories."""
    normalised = re.sub(r"\s+", " ", text.strip().lower())
    return hashlib.sha1(normalised.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Main scoring entry point
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ScoredInsight:
    content: str
    score: float                # 0.0 — 1.0
    memory_type: str            # reference | feedback | project | user
    slug: str
    hook: str
    fingerprint: str
    should_save: bool
    reasons: tuple[str, ...]    # Which signals fired


# Minimum length below which we don't bother scoring.
_MIN_CONTENT_CHARS = 80

# Threshold to trigger save (inclusive).
# Lowered 0.55 → 0.45 → 0.20: the Explanatory output style emits codebase-specific
# insights that score in the 0.15–0.35 range. The dedup fingerprint prevents
# duplicate saves; the rejection log enables future recalibration.
_SAVE_THRESHOLD = 0.20

# Weighted positive signal groups: (signals, label, per_hit_weight, max_contribution)
_POSITIVE_GROUPS = (
    (_TOKEN_ECONOMY_SIGNALS, "token_economy", 0.18, 0.35),
    (_MEMORY_GAP_SIGNALS, "memory_gap", 0.18, 0.30),
    (_REFERENCE_PATTERN_SIGNALS, "reference_pattern", 0.15, 0.30),
    (_SHELL_PATH_SIGNALS, "shell_path", 0.12, 0.20),
    (_NONOBVIOUS_SIGNALS, "nonobvious", 0.10, 0.25),
    (_EVIDENCE_SIGNALS, "evidence", 0.10, 0.20),
    (_FALSIFIABILITY_SIGNALS, "falsifiability", 0.12, 0.25),  # R0 gate reward
)


def _score_positives(content: str) -> tuple[float, list[str]]:
    """Sum weighted positive signals. Return (contribution, reasons)."""
    total = 0.0
    reasons: list[str] = []
    for signals, label, weight, cap in _POSITIVE_GROUPS:
        hits = _count_signals(content, signals)
        if hits:
            total += min(cap, weight * hits)
            reasons.append(f"{label}:{hits}")
    return total, reasons


def _score_negatives(content: str) -> tuple[float, list[str]]:
    """Sum weighted negative signals. Return (penalty, reasons)."""
    penalty = 0.0
    reasons: list[str] = []
    lower = content.lower().lstrip()
    if any(lower.startswith(p) for p in _BOILERPLATE_PREFIXES):
        penalty += 0.20
        reasons.append("boilerplate_prefix")
    eph = _count_signals(content, _EPHEMERAL_SIGNALS)
    if eph:
        penalty += 0.20 * eph
        reasons.append(f"ephemeral:{eph}")
    # R0: penalise overgeneralization markers ("obviously", "clearly", unconstrained
    # "always"/"never"). Capped at 0.15 so one marker doesn't kill a legitimate insight.
    overgen = _count_signals(content, _OVERGENERALIZATION_SIGNALS)
    if overgen:
        penalty += min(0.15, 0.08 * overgen)
        reasons.append(f"overgeneralization:{overgen}")
    return penalty, reasons


def score(content: str) -> ScoredInsight:
    """Score a single insight. Returns a ScoredInsight with should_save set."""
    if len(content.strip()) < _MIN_CONTENT_CHARS:
        return ScoredInsight(
            content=content, score=0.0, memory_type="reference",
            slug=_slug(content), hook=_hook(content),
            fingerprint=fingerprint(content), should_save=False,
            reasons=("too_short",),
        )
    pos, pos_reasons = _score_positives(content)
    neg, neg_reasons = _score_negatives(content)
    final = max(0.0, min(1.0, pos - neg))
    return ScoredInsight(
        content=content, score=final,
        memory_type=_classify(content),
        slug=_slug(content), hook=_hook(content),
        fingerprint=fingerprint(content),
        should_save=final >= _SAVE_THRESHOLD,
        reasons=tuple(pos_reasons + neg_reasons),
    )


# ---------------------------------------------------------------------------
# Dedup against existing MEMORY.md index
# ---------------------------------------------------------------------------


def load_existing_fingerprints(memory_dir: Path) -> set[str]:
    """Read all .md files in memory_dir, return a set of fingerprints.

    Reads the full file body (minus frontmatter) and fingerprints the
    canonical content.
    """
    fingerprints: set[str] = set()
    if not memory_dir.is_dir():
        return fingerprints

    for md_file in memory_dir.glob("*.md"):
        if md_file.name == "MEMORY.md":
            continue
        try:
            raw = md_file.read_text(encoding="utf-8")
        except OSError:
            continue
        # Strip YAML frontmatter if present
        body = raw
        if raw.startswith("---"):
            parts = raw.split("---", 2)
            if len(parts) >= 3:
                body = parts[2]
        fingerprints.add(fingerprint(body))

        # Also fingerprint the description from frontmatter for stronger dedup
        desc_match = re.search(r"^description:\s*(.+)$", raw, re.MULTILINE)
        if desc_match:
            fingerprints.add(fingerprint(desc_match.group(1)))

    return fingerprints


def is_duplicate(scored: ScoredInsight, existing: set[str]) -> bool:
    """Return True if the scored insight duplicates an existing memory."""
    return scored.fingerprint in existing
