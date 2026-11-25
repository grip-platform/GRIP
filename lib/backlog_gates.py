"""Critical reasoning gates for backlog processing.

These are mechanical guards — Python functions, not prompt instructions.
The AI cannot argue its way past them.
"""

from typing import Optional

URGENCY_MARKERS = {"urgent", "blocking", "asap", "today", "critical", "p0", "p1"}


import importlib
import importlib.util
import logging as _logging

_log = _logging.getLogger(__name__)


def _resolve_model(family: str, fallback: str) -> str:
    """Resolve a model id from HAL's canonical catalogue, fail-safe.

    Imported lazily so this mechanical-guard module can never fail to
    import if the resolver path is unavailable — it degrades to the
    verbatim fallback literal (same fail-safe contract as the resolver).

    A *missing* resolver module (HAL not on path) degrades silently — that
    is the designed fail-safe. A resolver module that is *present but
    broken* (e.g. a SyntaxError surfacing as ImportError) is a different
    failure: it is logged at WARNING so a broken resolver cannot rot
    undetected, then still degrades (the guard must never crash). This
    distinction is the AGORA-raised correctness fix on PR #2964.
    """
    for mod_name in ("lib.hal_model_resolver", "hal_model_resolver"):
        if importlib.util.find_spec(mod_name) is None:
            continue  # genuinely absent — try next path / fall through
        try:
            mod = importlib.import_module(mod_name)
            return mod.resolve(family, fallback)
        except Exception as exc:  # present but broken — surface, don't hide
            _log.warning(
                "hal_model_resolver (%s) present but failed (%s); "
                "degrading to verbatim fallback %r",
                mod_name, exc, fallback,
            )
            return fallback
    return fallback


def anti_sycophancy_gate(body: str, proposals: list[dict],
                         confidence_floor: float = 0.5,
                         max_actions: int = 3) -> list[dict]:
    """Deflate proposal importance. Bias toward doing less, not more.

    Gates:
    1. Confidence floor — drop low-confidence proposals
    2. Action count ceiling — max 3 per item
    3. Tired-engineer test — short input = max 1 action
    4. Priority deflation — P2 default unless urgency markers present
    """
    filtered = []
    word_count = len(body.split())

    for proposal in proposals:
        # Gate 1: Confidence floor
        if proposal.get("confidence", 0) < confidence_floor:
            continue

        # Gate 2: Action count ceiling
        if len(filtered) >= max_actions:
            break

        # Gate 3: Tired-engineer test
        if word_count < 20 and len(filtered) >= 1:
            break

        # Gate 4: Priority deflation
        priority = proposal.get("priority", "P2")
        if priority in ("P0", "P1"):
            if not _has_urgency_markers(body):
                proposal["priority"] = "P2"
                proposal["reasoning"] = proposal.get("reasoning", "") + \
                    " [deflated: no urgency markers in source]"

        filtered.append(proposal)
    return filtered


def _has_urgency_markers(text: str) -> bool:
    """Check if text contains urgency markers."""
    words = set(text.lower().split())
    return bool(words & URGENCY_MARKERS)


def gentner_connection_gate(item_body: str, candidate_text: str,
                            similarity_score: float,
                            similarity_threshold: float = 0.65) -> bool:
    """Reject connections that are surface-level rather than structural.

    Based on Gentner's Structure Mapping Theory:
    1. Similarity above threshold (necessary but not sufficient)
    2. High keyword overlap with mediocre embedding = surface match
    3. Usefulness checked by LLM in REASON stage (not here)
    """
    # Gate 1: Similarity floor
    if similarity_score < similarity_threshold:
        return False

    # Gate 2: Keyword overlap check
    item_words = set(item_body.lower().split())
    conn_words = set(candidate_text.lower().split())
    union = item_words | conn_words
    if not union:
        return False
    overlap_ratio = len(item_words & conn_words) / len(union)

    # High word overlap but mediocre similarity = surface match
    if overlap_ratio > 0.6 and similarity_score < 0.80:
        return False

    return True


def cost_gate(daily_spent: float, daily_ceiling: float = 0.50,
              item_word_count: int = 0,
              complex_threshold: int = 1000) -> dict:
    """Enforce cost ceiling and select appropriate model.

    Returns:
        dict with 'allowed' (bool) and 'model' (str)
    """
    if daily_spent >= daily_ceiling:
        return {"allowed": False, "model": None, "reason": "Daily ceiling reached"}

    if item_word_count > complex_threshold:
        model = _resolve_model("anthropic/claude-sonnet-", "claude-sonnet-4-6")
    else:
        model = _resolve_model("anthropic/claude-haiku-", "claude-haiku-4-5")

    return {"allowed": True, "model": model, "reason": "Within budget"}


def coherence_gate(text: str, min_length: int = 3,
                   max_gibberish_ratio: float = 0.5) -> bool:
    """Reject incoherent content (gibberish, corrupt extraction, encoding errors).

    Simple heuristic: ratio of non-alphanumeric characters to total.
    More sophisticated checks could use language detection.
    """
    if len(text.strip()) < min_length:
        return False

    # Count alphanumeric vs total
    alnum_count = sum(1 for c in text if c.isalnum() or c.isspace())
    total = len(text)
    if total == 0:
        return False

    # If more than half the text is non-alphanumeric, it's likely garbage
    alnum_ratio = alnum_count / total
    if alnum_ratio < (1.0 - max_gibberish_ratio):
        return False

    return True


def should_invoke_mesh(category: Optional[str], content_type: str,
                       word_count: int) -> bool:
    """Determine if item requires multi-perspective Broly mesh analysis."""
    if category in ("decision", "project"):
        return True
    if content_type == "document" and word_count > 2000:
        return True
    return False
