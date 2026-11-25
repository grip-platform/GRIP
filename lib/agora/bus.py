"""MessageBus — in-process router for AGORA conversations.

V1 is in-process (KISS — minimal dependencies, single machine). V2 will swap
in Redis or NATS pub/sub by implementing the same MessageBus interface;
agent and runner code does not change.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from lib.agora.message import Message


@dataclass
class ConversationState:
    """Per-conversation state held by the bus. Treat as read-only externally."""

    convo_id: str
    participants: list[str]
    messages: list[Message] = field(default_factory=list)
    stop_votes: set[str] = field(default_factory=set)


class MessageBus:
    """Routes messages between agents. NO decisions, NO synthesis, NO judging."""

    def __init__(self) -> None:
        self._conversations: dict[str, ConversationState] = {}

    def open(self, convo_id: str, participants: list[str]) -> None:
        self._conversations[convo_id] = ConversationState(
            convo_id=convo_id, participants=list(participants),
        )

    def post(self, convo_id: str, message: Message) -> list[str]:
        """Append message to conversation; return recipient agent_ids.

        A new message from an agent rescinds any prior STOP vote — speaking
        again means re-engaging, per AGORA's STOP-doesn't-remove semantics.
        """
        state = self._conversations[convo_id]
        state.messages.append(message)
        state.stop_votes.discard(message.sender_id)
        return self._recipients(state, message)

    def vote_stop(self, convo_id: str, agent_id: str) -> None:
        self._conversations[convo_id].stop_votes.add(agent_id)

    def quorum_reached(self, convo_id: str, threshold: float = 2 / 3) -> bool:
        state = self._conversations[convo_id]
        needed = max(1, math.ceil(len(state.participants) * threshold))
        return len(state.stop_votes) >= needed

    def history(self, convo_id: str) -> list[Message]:
        return list(self._conversations[convo_id].messages)

    @staticmethod
    def _recipients(state: ConversationState, message: Message) -> list[str]:
        if message.is_broadcast():
            return [p for p in state.participants if p != message.sender_id]
        return [r for r in message.recipients if r in state.participants]
