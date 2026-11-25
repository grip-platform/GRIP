"""AgoraSpeaker — top-level composite Speaker.

The 'Agent as an LLM' pattern. From outside, an Agora looks like an LLM —
one envelope in, one Response out. From inside, it routes to member Speakers
(typically Teams + LLMs + wrapped AgoraConversations) per a routing strategy.

Recursion is unbounded but TERMINATES because the composition graph is a
static DAG, validated at construction by the cycle guard.

Named agora_speaker.py (not agora.py) to avoid confusion with the lib.agora
package itself.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from lib.agora.speaker import Message, Response, Speaker, Synthesiser, default_synthesiser

# A RoutingStrategy returns the indices of members to dispatch to.
RoutingStrategy = Callable[[Message, list[Speaker]], list[int]]


def broadcast_strategy(msg: Message, members: list[Speaker]) -> list[int]:
    """Default — dispatch to all members."""
    return list(range(len(members)))


def first_strategy(msg: Message, members: list[Speaker]) -> list[int]:
    """Single-member dispatch — first member only."""
    return [0]


@dataclass
class AgoraSpeaker(Speaker):
    """Top-level composite. Routes Messages across members per strategy."""

    agora_id: str
    members: list[Speaker]
    routing: RoutingStrategy = field(default=broadcast_strategy)
    synthesiser: Synthesiser = field(default=default_synthesiser)

    def __post_init__(self) -> None:
        if not self.members:
            raise ValueError("AgoraSpeaker requires at least one member")
        self._guard_no_cycle(self, set())

    def _guard_no_cycle(self, node: Speaker, seen: set[int]) -> None:
        if id(node) in seen:
            raise ValueError(
                f"AgoraSpeaker {self.agora_id} contains a cycle through {node.speaker_id}"
            )
        seen = seen | {id(node)}
        members = getattr(node, "members", None)
        if not members:
            return
        for m in members:
            self._guard_no_cycle(m, seen)

    @property
    def kind(self) -> str:
        return "agora"

    @property
    def speaker_id(self) -> str:
        return self.agora_id

    def send_message(self, msg: Message) -> Response:
        chosen_indices = self.routing(msg, self.members)
        if not chosen_indices:
            chosen_indices = [0]  # post-condition guard
        chosen = [self.members[i] for i in chosen_indices]
        sub = [member.send_message(msg) for member in chosen]
        content = self.synthesiser(sub)
        if not content:
            content = "[empty]"
        return Response(
            id=msg.id,
            content=content,
            kind=self.kind,
            speaker_id=self.speaker_id,
            sub_responses=sub,
            metadata={"chosen_count": len(chosen), "total_members": len(self.members)},
        )
