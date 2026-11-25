"""HAL Council — profile definitions + family-dedupe agreement.

The base `HALCouncil` class in `lib.hal.moe` is a universal MoE runner.
Specific council lineups and their weight tables live here so the profile
configuration is versionable, testable, and independent of the runner.

### `HAL_COUNCIL_REVIEW` profile

Hard-coded 4-LLM lineup for the HAL Council unified PR reviewer:
- Opus 4.7 (1M) — structural / SOLID / GRASP (Anthropic family)
- Sonnet 4.6 (1M) — integration / YAGNI / DRY (Anthropic family)
- Haiku 4.5 — fact-check PR body claims (Anthropic family)
- Groq Llama 3.3 70B — independent outsider, no GRIP system prompt

**Gemini is NOT on the council.** Gemini's only seat is the judge chair
(see `lib/gemini_judge.py::judge_permanent`). Including Gemini as a
council member would mean the same model scores then judges — conflict of
interest.

### Family-deduped agreement (plan senior-infra gotcha §7)

The three Anthropic models (Opus/Sonnet/Haiku) come from the same training
lineage. They correlate by construction. Counting them as three independent
votes inflates apparent agreement whenever they align, which is most of the
time. This is the "artificial consensus" trap.

`family_deduped_agreement` collapses all Anthropic votes to a single
representative (longest response text), then computes agreement across
distinct families only. A 3-Anthropic + 1-Llama council with Anthropic
unanimous and Llama disagreeing becomes 2 distinct votes with 50% agreement,
not 4 votes with 75% agreement.

### Weight table (plan lines 171-182)

8 dimensions summing to 1.0 — mechanical 0.45 / LLM 0.55 — so LLM sycophancy
cannot push the score past the mechanical floor.

Pure stdlib. No external deps.
"""
from __future__ import annotations

from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# Family classification
# ---------------------------------------------------------------------------


ANTHROPIC_FAMILY_PREFIXES: tuple[str, ...] = (
    "anthropic/",
    "claude-",
    "anthropic-claude-",
)

LLAMA_FAMILY_PREFIXES: tuple[str, ...] = (
    "groq/",
    "llama-",
    "meta/",
    "meta-llama/",
)

# Groq is a HOST, not a lineage. It served Llama until 2026-06-17 and now
# serves an OpenAI-lineage model, so "groq/" alone can no longer stand in for
# a family. Where the address states its lineage, that lineage wins over the
# host prefix — otherwise dedupe_by_family would collapse an OpenAI vote and a
# Llama vote into one and silently spend a genuinely independent voice.
OPENAI_FAMILY_PREFIXES: tuple[str, ...] = (
    "openai/",
    "gpt-",
    "o1-",
    "o3-",
)

GEMINI_FAMILY_PREFIXES: tuple[str, ...] = (
    "gemini/",
    "google/",
    "gemini-",
)


def model_family(model_address: str) -> str:
    """Classify a model address into a training-lineage family.

    Returns one of: "anthropic", "openai", "llama", "gemini", "unknown".
    Used for deduplication before agreement computation — votes from the
    same family are collapsed to one representative (see plan §7).
    """
    if not model_address:
        return "unknown"
    addr = model_address.lower()
    if any(addr.startswith(p) for p in ANTHROPIC_FAMILY_PREFIXES):
        return "anthropic"
    # Lineage before host: strip a leading host segment and look again, so
    # "groq/openai/gpt-oss-120b" reads as openai rather than as its host.
    body = addr.split("/", 1)[1] if addr.count("/") >= 2 else addr
    if any(body.startswith(p) or addr.startswith(p) for p in OPENAI_FAMILY_PREFIXES):
        return "openai"
    if any(addr.startswith(p) for p in LLAMA_FAMILY_PREFIXES):
        return "llama"
    if any(addr.startswith(p) for p in GEMINI_FAMILY_PREFIXES):
        return "gemini"
    return "unknown"


# ---------------------------------------------------------------------------
# Council profile
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CouncilProfile:
    """A named council configuration — model lineup + weight table."""

    name: str
    description: str
    models: tuple[str, ...]
    weights: dict[str, float] = field(default_factory=dict)

    def weights_sum(self) -> float:
        return round(sum(self.weights.values()), 6)

    def assert_weights_normalised(self, tolerance: float = 1e-6) -> None:
        """Raise ValueError if weights do not sum to 1.0 (+/- tolerance)."""
        total = self.weights_sum()
        if abs(total - 1.0) > tolerance:
            raise ValueError(
                f"CouncilProfile({self.name!r}) weights sum to {total}, expected 1.0"
            )


HAL_COUNCIL_REVIEW = CouncilProfile(
    name="hal-council-review",
    description=(
        "Four-LLM Mixture-of-Experts reviewer for HAL Council PRs. "
        "Anthropic family (Opus/Sonnet/Haiku) via Claude Code subscription "
        "routing (zero marginal cost). Groq Llama 3.3 70B as independent "
        "outsider with NO GRIP system prompt. Gemini is the judge, not a "
        "council member."
    ),
    models=(
        "anthropic/claude-opus-4-7",
        "anthropic/claude-sonnet-4-6",
        "anthropic/claude-haiku-4-5",
        "groq/openai/gpt-oss-120b",
    ),
    weights={
        # LLM-measured (0.55 total)
        "correctness": 0.20,
        "design_principles": 0.15,
        "hypothesis_quality": 0.10,
        "documentation": 0.05,
        "mermaid_clarity": 0.05,
        # Mechanical (0.45 total)
        "complexity": 0.20,
        "ast_diff_scope": 0.10,
        "hypothesis_verification": 0.10,
        "test_coverage_delta": 0.05,
    },
)


# ---------------------------------------------------------------------------
# Family-deduped agreement
# ---------------------------------------------------------------------------


def _response_text(vote) -> str:
    """Extract the text body of a vote, safely."""
    response = getattr(vote, "response", None)
    if response is None:
        return ""
    return getattr(response, "text", "") or ""


def _vote_family(vote) -> str:
    """Family for a vote, via its `model` attribute."""
    return model_family(getattr(vote, "model", "") or "")


def dedupe_by_family(votes) -> list:
    """Collapse votes from the same family to the longest-response repr.

    Preserves order of first appearance per family. If two votes share a
    family, the one with the longer `response.text` wins. Empty-family
    votes (`model_family(...) == "unknown"`) are kept as-is — unknown
    providers don't risk correlation-by-training-lineage.
    """
    seen: dict[str, object] = {}
    unknowns: list = []
    for vote in votes:
        fam = _vote_family(vote)
        if fam == "unknown":
            unknowns.append(vote)
            continue
        existing = seen.get(fam)
        if existing is None:
            seen[fam] = vote
            continue
        if len(_response_text(vote)) > len(_response_text(existing)):
            seen[fam] = vote
    return list(seen.values()) + unknowns


def _jaccard(a: set, b: set) -> float:
    if not a and not b:
        return 1.0
    union = a | b
    if not union:
        return 0.0
    return len(a & b) / len(union)


def family_deduped_agreement(votes) -> float:
    """Jaccard-style agreement score across distinct LLM families only.

    Collapses Anthropic family (Opus/Sonnet/Haiku) to one vote before
    scoring. Prevents artificial-consensus inflation when the three
    Anthropic models agree by training correlation alone (plan senior-infra
    gotcha §7).

    Returns:
        Score in [0.0, 1.0]. 1.0 when there's only one (deduped) vote.
    """
    deduped = dedupe_by_family(votes)
    if len(deduped) < 2:
        return 1.0

    texts = [set(_response_text(v).lower().split()) for v in deduped]
    ref = texts[0]
    if not ref and not any(texts[1:]):
        return 1.0

    scores = [_jaccard(ref, t) for t in texts[1:]]
    return sum(scores) / len(scores)


# ---------------------------------------------------------------------------
# Introspection helpers
# ---------------------------------------------------------------------------


def council_has_gemini(profile: CouncilProfile) -> bool:
    """True if any profile model classifies as Gemini family.

    HAL_COUNCIL_REVIEW asserts this is False — Gemini is the judge, not a
    council member. Tests use this as a regression guard.
    """
    return any(model_family(m) == "gemini" for m in profile.models)


def council_families(profile: CouncilProfile) -> tuple[str, ...]:
    """Distinct families represented in the profile, in first-appearance order."""
    seen: list[str] = []
    for m in profile.models:
        fam = model_family(m)
        if fam not in seen:
            seen.append(fam)
    return tuple(seen)


__all__ = [
    "ANTHROPIC_FAMILY_PREFIXES",
    "LLAMA_FAMILY_PREFIXES",
    "GEMINI_FAMILY_PREFIXES",
    "CouncilProfile",
    "HAL_COUNCIL_REVIEW",
    "council_families",
    "council_has_gemini",
    "dedupe_by_family",
    "family_deduped_agreement",
    "model_family",
]
