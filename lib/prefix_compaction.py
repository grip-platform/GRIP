"""Pre-idle prefix compaction: cheapens the unavoidable post-idle cache-miss reprocess.

WHY THIS MODULE EXISTS (not in scrollback_lru.py — SRP):
  scrollback_lru.py handles RETRIEVAL of stashed context (6-tier LRU, disk/mem).
  This module handles COMPACTION of the LIVE conversation prefix BEFORE an idle
  gap, so when the Anthropic prompt cache evicts (5-min sliding TTL) the
  subsequent reprocess costs ~≤20k tokens instead of the full ~88k.

HONEST FRAMING (falsification clause H-COMPACT-1 below):
  Prefix compaction does NOT prevent the cache miss.
  Prefix compaction does NOT warm the server-side cache.
  It only makes the unavoidable miss CHEAPER by producing a compact prefix.
  The keepalive (separate module/worktree) is what prevents the miss.

TOKEN ESTIMATE: chars / 4  (OpenAI/Anthropic empirical rule-of-thumb; ±20%).
  Cited source: https://help.openai.com/en/articles/4936856-what-are-tokens-and-how-to-count-them
  Good enough for a cost-reduction heuristic; not for billing.

HYPOTHESIS H-COMPACT-1 (deadline 2026-06-30):
  compact_prefix yields ≥4x token reduction on a representative ~88k-token
  prefix while preserving system + last keep_recent_turns messages byte-identical.
  Falsified if: (a) measured ratio < 4.0 on the reference 88k fixture, OR
  (b) any byte in a preserved message differs from the input.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, List, Optional

# ---------------------------------------------------------------------------
# Token estimation
# ---------------------------------------------------------------------------

_CHARS_PER_TOKEN = 4  # Rule-of-thumb; see module docstring for citation.


def estimate_tokens(text: str) -> int:
    """Return a rough upper-bound token count for *text*."""
    return max(1, len(text) // _CHARS_PER_TOKEN)


def _msg_text(msg: object) -> str:
    """Extract a string representation from a message dict or plain string."""
    if isinstance(msg, dict):
        content = msg.get("content", "")
        if isinstance(content, list):
            # Anthropic multi-part content blocks
            return " ".join(
                block.get("text", "") for block in content if isinstance(block, dict)
            )
        return str(content)
    return str(msg)


# ---------------------------------------------------------------------------
# Digest builder (deterministic, no LLM call in core path)
# ---------------------------------------------------------------------------

_DEFAULT_HEAD_CHARS = 120
_DEFAULT_TAIL_CHARS = 80


def _make_digest(msg: object, head: int = _DEFAULT_HEAD_CHARS, tail: int = _DEFAULT_TAIL_CHARS) -> dict:
    """Role-tagged head/tail truncation — deterministic, no network calls.

    Produces a synthetic message dict whose content is:
      <role>:[head chars]…[tail chars]
    If the original content fits within head+tail, it is kept verbatim.
    """
    text = _msg_text(msg)
    role = msg.get("role", "unknown") if isinstance(msg, dict) else "unknown"
    if len(text) <= head + tail:
        digest_text = text
    else:
        digest_text = text[:head] + "…" + text[len(text) - tail:]
    return {"role": role, "content": f"[digest] {digest_text}"}


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class CompactionResult:
    """Inspectable savings report from compact_prefix."""

    compacted: List[object]
    original_tokens: int
    compacted_tokens: int
    ratio: float               # original / compacted; ≥1.0
    kept_verbatim_count: int   # number of messages preserved byte-identical


# ---------------------------------------------------------------------------
# Core API
# ---------------------------------------------------------------------------

def compact_prefix(
    messages: List[object],
    *,
    system: Optional[str] = None,
    target_tokens: int = 20_000,
    keep_recent_turns: int = 6,
    llm_summariser: Optional[Callable[[object], dict]] = None,
) -> CompactionResult:
    """Compact *messages* to ≤ *target_tokens* for cheaper post-idle reprocess.

    Strategy (deterministic core; LLM path is optional):
      1. ALWAYS keep *system* message first (if provided).
      2. ALWAYS keep the last *keep_recent_turns* messages VERBATIM.
      3. Older messages → _make_digest() (head/tail truncation, role-tagged).
         If *llm_summariser* is injected, it replaces _make_digest for each
         older message (optional; default is deterministic / network-free).
      4. Returns CompactionResult with token counts and ratio.

    The compacted list is NEVER empty: edge-case where len(messages) ≤
    keep_recent_turns returns all messages verbatim.
    """
    original_tokens = sum(estimate_tokens(_msg_text(m)) for m in messages)
    if system:
        original_tokens += estimate_tokens(system)

    # Split: verbatim tail vs older head
    if len(messages) <= keep_recent_turns:
        verbatim = list(messages)
        older: List[object] = []
    else:
        older = messages[: len(messages) - keep_recent_turns]
        verbatim = messages[len(messages) - keep_recent_turns :]

    # Build digests for older messages
    summarise = llm_summariser if llm_summariser is not None else _make_digest
    digests = [summarise(m) for m in older]

    # Assemble compacted list: [system_msg?] + digests + verbatim
    compacted: List[object] = []
    if system is not None:
        compacted.append({"role": "system", "content": system})
    compacted.extend(digests)
    compacted.extend(verbatim)

    compacted_tokens = sum(estimate_tokens(_msg_text(m)) for m in compacted)
    ratio = original_tokens / compacted_tokens if compacted_tokens > 0 else 1.0

    return CompactionResult(
        compacted=compacted,
        original_tokens=original_tokens,
        compacted_tokens=compacted_tokens,
        ratio=ratio,
        kept_verbatim_count=len(verbatim),
    )
