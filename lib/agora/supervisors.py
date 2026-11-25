"""AGORA supervisors — scoped meta-watchers with bounded intervention powers.

V1 implements the Falsifier prototype as a mechanical gate (Stage 1 of the
hybrid pattern locked in via /hitl decision matrix 2026-05-02). Stage 2 (LLM
agent escalation) and the four other supervisors (Hallucination, Sycophancy,
Domain Drift, Agent Dominance, Cascade Depth) extend this same Intervention
shape — they do NOT replace it.

Bounded power principle: no supervisor can terminate a conversation. The
strongest intervention is "inject corrective context" or "force regenerate."
Termination remains the participants' decision via quorum.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from lib.agora.message import Message

_UNIVERSAL_CLAIMS = re.compile(
    r"\b(always|never|every|all|none|proves|prove|disproves)\b", re.I,
)
_STRUCTURAL_MAPPING = re.compile(
    r"\b(is like|is the same as|maps to|equivalent to|just like)\b", re.I,
)


@dataclass
class Intervention:
    """A supervisor's intervention on a message. Logged for post-hoc audit."""

    supervisor: str
    message_id: str
    trigger: str
    correction: str


def falsifier_gate(message: Message) -> Intervention | None:
    """Stage 1 mechanical Falsifier gate.

    Detects unfalsifiable language patterns. Returns a corrective intervention
    if triggers found, None otherwise. Stage 2 (LLM agent escalation) runs
    only when this gate flags something.
    """
    triggers = []
    if _UNIVERSAL_CLAIMS.search(message.content):
        triggers.append("universal claim language")
    if _STRUCTURAL_MAPPING.search(message.content):
        triggers.append("structural mapping language")
    if not triggers:
        return None
    correction = (
        f"Falsifier note for {message.sender_id}: {'; '.join(triggers)}. "
        "State the falsification condition for your claim, "
        "or qualify scope to what the evidence actually supports."
    )
    return Intervention(
        supervisor="falsifier",
        message_id=message.msg_id,
        trigger="; ".join(triggers),
        correction=correction,
    )
