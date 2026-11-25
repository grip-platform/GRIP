"""TeamSpeaker — composite Speaker that fans a Message out to its members.

Members can be ANY Speaker (LLM, Agent, Team, Agora) — recursion is structural.
Synthesiser is injected so different Teams can use different aggregation strategies
(concat / debate / vote / weighted) without changing the interface.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from lib.agora.speaker import Message, Response, Speaker, Synthesiser, default_synthesiser


@dataclass
class TeamSpeaker(Speaker):
    """Composite Speaker. Fan-out to members, synthesise into one Response."""

    team_id: str
    members: list[Speaker]
    synthesiser: Synthesiser = field(default=default_synthesiser)

    def __post_init__(self) -> None:
        if not self.members:
            raise ValueError("TeamSpeaker requires at least one member")
        # Cycle guard — a Team must not contain itself transitively.
        self._guard_no_cycle(self, set())

    def _guard_no_cycle(self, node: Speaker, seen: set[int]) -> None:
        if id(node) in seen:
            raise ValueError(
                f"TeamSpeaker {self.team_id} contains a cycle through {node.speaker_id}"
            )
        seen = seen | {id(node)}
        members = getattr(node, "members", None)
        if not members:
            return
        for m in members:
            self._guard_no_cycle(m, seen)

    @property
    def kind(self) -> str:
        return "team"

    @property
    def speaker_id(self) -> str:
        return self.team_id

    def send_message(self, msg: Message) -> Response:
        # No isinstance — every member is a Speaker, just call .send_message.
        sub = [member.send_message(msg) for member in self.members]
        content = self.synthesiser(sub)
        if not content:
            content = "[empty]"
        return Response(
            id=msg.id,
            content=content,
            kind=self.kind,
            speaker_id=self.speaker_id,
            sub_responses=sub,
            metadata={"member_count": len(self.members)},
        )
