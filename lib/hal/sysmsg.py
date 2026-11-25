"""Mid-conversation system-message primitive (Opus 4.8, GA — no beta header).

Opus 4.8 accepts a ``role:"system"`` message immediately after a user turn in
the ``messages`` array (platform.claude.com/docs/en/about-claude/models/whats-new-claude-4-8).
That lets an agentic loop append an *instruction delta* without restating the
full system prompt — preserving prompt-cache hits on the earlier turns and
cutting input cost on every subsequent turn. For GRIP, whose system prompt is
enormous (CLAUDE.md + the whole rules/* stack + the HOLISTIC kernel), restating
it per turn is the single largest recurring input-token line on any long
``/auto`` / ``/converge`` / ``/rsi`` / council / relay loop.

This shim (syscall doctrine) is the one stable seam for that capability: pure,
trivially mockable, and **monotone** — it only ever APPENDS to (or folds into)
the *current* turn, never editing an earlier turn, which is precisely what
keeps each earlier turn's cache breakpoint warm. It is provider-capability
gated with a safe degrade for providers that do not support a mid-array system
turn.

Placement rule (4.8): a system delta is only valid immediately after a user
turn. ``apply_update`` enforces this by construction — it emits a system turn
only when the last message is a user turn; otherwise it degrades to folding the
delta into the current user turn (or appending one), which is valid for strict
role-alternation providers and still cache-preserving on earlier turns.

Limitations doc (placement rules): platform.claude.com/docs/en/build-with-claude/mid-conversation-system-messages#limitations
"""
from dataclasses import dataclass

from lib.hal.types import ChatMessage

# Providers whose Messages API accepts a role:"system" entry mid-array.
# Frozen constant, NOT a config loader (KISS/YAGNI) — extend the literal at the
# second supporting provider, per the canonical "frozen constant over loader" rule.
_SUPPORTS_MIDCONV_SYSTEM = frozenset({"anthropic"})

_DEGRADE_PREFIX = "[updated instructions] "


@dataclass(frozen=True)
class SysMsgUpdate:
    """One cache-preserving instruction update.

    ``text`` is the DELTA only — never the full system prompt. Sending the full
    prompt here would defeat the entire purpose (and fails the Goodhart test).
    """

    text: str


def supports_midconv_system(provider: str) -> bool:
    """True if ``provider`` accepts a role:"system" message mid-``messages``."""
    return provider in _SUPPORTS_MIDCONV_SYSTEM


def apply_update(
    messages: list[ChatMessage],
    update: SysMsgUpdate,
    *,
    provider: str,
) -> list[ChatMessage]:
    """Return a NEW message list with ``update`` applied cache-preservingly.

    Native path (provider supports a mid-array system turn AND the last message
    is a user turn): append a ``role:"system"`` ChatMessage carrying the delta.

    Degrade path (otherwise): fold the delta into the current user turn, or
    append a user turn if the last message is not a user turn. Either way the
    delta reaches the model without restating the system prompt.

    Pure — never mutates ``messages``. Earlier turns are returned byte-identical,
    so their prompt-cache breakpoints survive (the whole point).
    """
    last_is_user = bool(messages) and messages[-1].role == "user"

    if supports_midconv_system(provider) and last_is_user:
        return [*messages, ChatMessage(role="system", content=update.text)]

    if last_is_user:
        # Fold into the current (uncached) user turn — keeps role alternation
        # valid for strict providers; earlier turns stay byte-identical.
        folded = ChatMessage(
            role="user",
            content=f"{messages[-1].content}\n\n{_DEGRADE_PREFIX}{update.text}",
        )
        return [*messages[:-1], folded]

    return [*messages, ChatMessage(role="user", content=f"{_DEGRADE_PREFIX}{update.text}")]
