"""Dispatch kernel — capability classifier (pure function, no LLM, no I/O).

The PURE half of the Hybrid kernel (V>> HITL Q3, 2026-05-15): the capability
axis is a binary physical fact — a spawned subagent cannot mutate the
spawning session's private state — so a correct deterministic rule beats a
classifier at zero per-decision cost, zero latency, zero nondeterminism.
The economic axis (the ~3:1 work/floor judgement) is a separate bounded-LLM
path (C1b, deferred) because keyword presence is provably uncorrelated with
token volume and regex cannot estimate it.

Doctrine: rules/dispatch-economics.md (Gate 1). Plan:
plans/dispatch-kernel-mechanical-enforcement.md. Hypotheses H487-H490.

Falsification (H488 / H-DISP-2): if a pure-function classifier cannot reach
>=90% precision AND <2% false-positive on a real dispatch-log replay, the
capability gate stays WARN-only forever and never escalates to DENY. This
module is deterministic by construction, so its precision IS measurable by
replay — the determinism is the falsification instrument, not an obstacle to
it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Tools that physically cannot be invoked from a spawned subagent: they
# mutate the spawning session's private state. Exact-name match ⇒ "certain"
# confidence — this is physics (rules/dispatch-economics.md Gate 1 table),
# not a heuristic. TaskStop and Agent are deliberately ABSENT: the canonical
# table places them on the "crosses the boundary — delegate freely" side.
_SESSION_STATE_TOOLS = frozenset(
    {
        "taskcreate",
        "tasklist",
        "taskget",
        "taskupdate",
        "askuserquestion",
        "exitplanmode",
        "enterplanmode",
        "schedulewakeup",
    }
)

# Natural-language paraphrases of session-task-list / conversation mutation.
# Heuristic ⇒ "heuristic" confidence (lower than an exact tool-name hit). The
# devil council surfaced the false-negative family (the originating incident
# was an NL steering instruction, not the literal token "TaskUpdate"); these
# catch it. WARN-only this week, so a false positive is an ignorable advisory,
# never a block — that is what makes including fuzzy patterns safe.
_PARAPHRASE_PATTERNS = (
    r"\b(?:prune|clear|clean|tidy|reconcile|groom)\b[^.]{0,40}"
    r"\b(?:task list|task store|backlog|work tracker|to-?do list)\b",
    r"\bmark\b[^.]{0,40}\b(?:tasks?|items?|to-?dos?)\b[^.]{0,20}"
    r"\b(?:done|complete|completed)\b",
    r"\b(?:update|edit|mutate|rewrite)\b[^.]{0,30}"
    r"\b(?:the conversation|session state|the task (?:list|store))\b",
    r"\breconcile\b[^.]{0,40}\b(?:what (?:actually )?shipped|active work)\b",
)

_PARAPHRASE_RE = re.compile("|".join(_PARAPHRASE_PATTERNS), re.IGNORECASE)
_TOOL_TOKEN_RE = re.compile(r"[a-z]+", re.IGNORECASE)

_ADVICE = (
    "Mutates session-private state — a subagent physically cannot do this. "
    "Do it on the main thread directly. The most token-efficient agent is "
    "no agent (rules/dispatch-economics.md Gate 1)."
)


@dataclass(frozen=True)
class CapabilityVerdict:
    """Result of the pure capability check.

    confidence is "certain" for an exact main-session-only tool-name hit
    (physics), "heuristic" for an NL paraphrase match (fuzzy, WARN-only), or
    "none" when the work is delegatable on the capability axis. The honest
    confidence split answers the devil council's objection 3: never present a
    heuristic guess with the authority of a physical certainty.
    """

    undelegatable: bool
    confidence: str
    marker: str | None
    reason: str
    advice: str


def _match_tool_markers(prompt: str) -> str | None:
    """Return the first session-state tool name present, or None.

    Tokenised word match (not substring) so "the task store" does not
    spuriously hit "taskstore"-like fragments and "multitasking" never
    matches "task". Exact-token discipline keeps false positives low.
    """
    for token in _TOOL_TOKEN_RE.findall(prompt.lower()):
        if token in _SESSION_STATE_TOOLS:
            return token
    return None


def capability_verdict(prompt: str) -> CapabilityVerdict:
    """Classify the capability axis of an intended dispatch. Pure, no I/O.

    Order matters: an exact tool-name match is physics ("certain") and takes
    precedence over the fuzzy paraphrase match ("heuristic").
    """
    text = prompt or ""
    marker = _match_tool_markers(text)
    if marker is not None:
        return CapabilityVerdict(
            undelegatable=True,
            confidence="certain",
            marker=marker,
            reason=f"names main-session-only tool '{marker}'",
            advice=_ADVICE,
        )
    if _PARAPHRASE_RE.search(text):
        return CapabilityVerdict(
            undelegatable=True,
            confidence="heuristic",
            marker=None,
            reason="reads as session-task-list / conversation mutation",
            advice=_ADVICE,
        )
    return CapabilityVerdict(
        undelegatable=False,
        confidence="none",
        marker=None,
        reason="no session-state mutation detected on the capability axis",
        advice="",
    )


def recommended_tier(prompt: str) -> int | None:
    """Week-one tier advice. ONLY the capability axis is enforced now.

    Returns 0 (do it on the main thread, no agent) when the work is
    undelegatable. Returns None when the work is capability-clean — the
    economic tier (-1/1/2) is intentionally NOT computed here: that is the
    bounded-LLM C1b path (V>> HITL Q3 Hybrid), deferred to a later step. None
    means "not enforced this week", honestly, rather than a fabricated tier.
    """
    return 0 if capability_verdict(prompt).undelegatable else None
