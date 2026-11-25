"""AGORA message and decision types — wire format for peer-LLM conversation."""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum

SYSTEM_SENDER = "system"
"""Reserved sender_id used by the runner for synthesised stop-signal messages.
Never shadowed by an agent_id; used by the round-robin scheduler's skip-self
check to ensure stop-signals always trigger a different actor."""


class Decision(Enum):
    REPLY = "reply"
    STOP = "stop"
    DEFER = "defer"


@dataclass
class Message:
    sender_id: str
    recipients: list[str]
    content: str
    intent: str | None = None
    references: list[str] = field(default_factory=list)
    timestamp: float = field(default_factory=time.time)
    msg_id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])

    def is_broadcast(self) -> bool:
        return "*" in self.recipients


@dataclass
class Response:
    decision: Decision
    content: str
    follow_up_intent: str | None = None
    defer_to: list[str] | None = None
    defer_question: str | None = None
    stop_rationale: str | None = None

    def __post_init__(self) -> None:
        if self.decision == Decision.DEFER:
            if not self.defer_to or not self.defer_question:
                raise ValueError("DEFER requires defer_to and defer_question")
        if self.decision == Decision.STOP and not self.stop_rationale:
            raise ValueError("STOP requires stop_rationale")
