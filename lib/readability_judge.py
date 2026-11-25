"""Readability judge — cross-model validation for plain language output.

Uses an independent model (local Llama 3B or Groq free tier) to judge
whether GRIP's output is understandable by a general human being.
Not circular: the judge has no GRIP context and different training data.

Two-layer check:
  1. Deterministic pre-filter catches obviously incomplete messages
     (standalone comparatives without context, e.g. "moderate difference").
  2. LLM judge checks vocabulary and overall comprehensibility.

Usage:
    from readability_judge import is_readable

    result = is_readable("Session: Running well. Completing 9 tasks per hour.")
    # {"readable": True, "reason": "Clear and self-explanatory"}

    result = is_readable("DELIGHT v2: 0.748 flow=1.00 vel=9.3/hr")
    # {"readable": False, "reason": "Technical metrics without explanation"}

    result = is_readable("moderate difference")
    # {"readable": False, "reason": "Comparative term without reference context"}
"""
import json
import logging
import re

logger = logging.getLogger(__name__)

# ── Deterministic Pre-Filter ─────────────────────────────────────────

_UNANCHORED_COMPARATIVES = re.compile(
    r"\b(moderate|significant|large|small|negligible|above|below|"
    r"higher|lower|better|worse)\s+"
    r"(difference|change|improvement|degradation|effect|variation)\b",
    re.IGNORECASE,
)


def _pre_filter(text: str) -> dict | None:
    """Catch obviously incomplete messages before LLM judge.

    Flags short texts that contain comparative terms without a reference
    noun (e.g. "moderate difference" with no "between X and Y").
    Returns a failure dict, or None to pass through to LLM judge.
    """
    if _UNANCHORED_COMPARATIVES.search(text) and len(text.split()) < 8:
        return {"readable": False, "reason": "Comparative term without reference context"}
    return None


# ── LLM Judge Prompt ─────────────────────────────────────────────────

_JUDGE_PROMPT = (
    "You are judging whether text is understandable AND informative to a "
    "general person with no specialist knowledge.\n\n"
    "Check TWO things:\n"
    "1. Are all words and phrases understandable without specialist training?\n"
    "2. Does the text provide enough context to be meaningful? "
    '(e.g., "moderate difference" is understandable but meaningless without '
    "knowing what is being compared.)\n\n"
    "Answer ONLY with a JSON object: "
    '{"readable": true} or {"readable": false, "reason": "brief reason"}. '
    "No other text.\n\nText to judge:\n"
)


def _judge_local(text: str, engine=None) -> dict:
    """Judge readability using local Llama 3.2 3B (zero cost, ~1s)."""
    if engine is None:
        from llama_engine import LlamaEngine
        engine = LlamaEngine(n_ctx=512)
    response = engine.generate(_JUDGE_PROMPT + text, max_tokens=80, temperature=0)
    return _parse_response(response)


def _judge_groq(text: str, provider=None) -> dict:
    """Judge readability using Groq free tier (Llama 3.1 8B)."""
    if provider is None:
        from providers.groq import GroqProvider
        provider = GroqProvider()
    result = provider.generate(
        _JUDGE_PROMPT + text,
        model="llama-3.1-8b-instant",
        temperature=0, max_tokens=80)
    return _parse_response(result.text)


def _parse_response(raw: str) -> dict:
    """Extract JSON from model response, with fallback."""
    raw = raw.strip()
    start = raw.find("{")
    end = raw.rfind("}") + 1
    if start >= 0 and end > start:
        try:
            return json.loads(raw[start:end])
        except json.JSONDecodeError:
            pass
    return {"readable": True, "reason": "Could not parse judge response"}


def is_readable(text: str, prefer_local: bool = True) -> dict:
    """Judge whether text is understandable by a general human being.

    Two layers: deterministic pre-filter (catches standalone comparatives),
    then LLM judge (checks vocabulary and completeness).
    Returns {"readable": bool, "reason": str}.
    """
    pre = _pre_filter(text)
    if pre is not None:
        return pre

    if prefer_local:
        try:
            return _judge_local(text)
        except (FileNotFoundError, ImportError, OSError) as exc:
            logger.debug("Local judge unavailable (%s), trying Groq", exc)

    try:
        return _judge_groq(text)
    except Exception as exc:
        logger.debug("Groq judge failed: %s", exc)
        return {"readable": True, "reason": f"Judge unavailable: {exc}"}
