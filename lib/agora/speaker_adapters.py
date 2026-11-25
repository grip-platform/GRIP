"""Speaker adapters — bridges between Speaker envelopes and existing AGORA types.

The existing lib.agora module has its own peer-protocol Message/Response shape
(broadcast routing, REPLY/STOP/DEFER decisions). The Speaker abstraction has
a different shape (single envelope in, single Response out, no decision flags).

These adapters let an AgoraAgent OR a full ConversationRunner be used as a
Speaker without modifying the existing types. Composition over inheritance.

LSP-safe: each adapter implements Speaker contract via Wrapper, never claims
to be a subtype of AgoraAgent.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

# Import from sibling modules — explicit paths to avoid name collisions
# between lib.agora.speaker.Message and lib.agora.message.Message.
from lib.agora.agent import AgoraAgent
from lib.agora.bus import MessageBus
from lib.agora.message import Message as PeerMessage
from lib.agora.runner import ConversationRunner, RunnerLimits
from lib.agora.speaker import Message, Response, Speaker

# DI seam — callers can inject a different runner factory (test stub, alternate
# orchestrator, instrumented variant) without subclassing the adapter.
RunnerFactory = Callable[[MessageBus, RunnerLimits], ConversationRunner]


def default_runner_factory(bus: MessageBus, limits: RunnerLimits) -> ConversationRunner:
    return ConversationRunner(bus, limits)


@dataclass
class AgoraAgentSpeaker(Speaker):
    """Wraps a single AgoraAgent so it satisfies the Speaker contract.

    On send_message: synthesises a one-shot peer Message addressed to the
    wrapped agent, calls .receive() with empty history, returns the agent's
    Response content.

    Use when you want one Agora-flavoured agent participating in a Speaker
    composition (Team or AgoraSpeaker member).
    """

    agent: AgoraAgent
    sender_id: str = "speaker_caller"

    @property
    def kind(self) -> str:
        return "agent"

    @property
    def speaker_id(self) -> str:
        return self.agent.agent_id

    def send_message(self, msg: Message) -> Response:
        peer_msg = PeerMessage(
            sender_id=self.sender_id,
            recipients=[self.agent.agent_id],
            content=msg.payload,
            intent=msg.metadata.get("intent") if msg.metadata else None,
        )
        peer_response = self.agent.receive(peer_msg, history=[])
        content = peer_response.content or "[empty]"
        return Response(
            id=msg.id,
            content=content,
            kind=self.kind,
            speaker_id=self.speaker_id,
            sub_responses=None,
            metadata={"decision": peer_response.decision.value},
        )


@dataclass
class AgoraConversationSpeaker(Speaker):
    """Wraps a full multi-agent conversation as a single Speaker.

    On send_message: spawns a ConversationRunner with the envelope payload as
    the opening peer Message, lets agents converse to quorum or cap, returns
    a synthesised summary of the final conversation as the Response content.

    This is the canonical 'council exposed as an LLM' pattern V>>'s Agora
    framing describes — outside callers see one envelope; inside, a peer
    conversation runs.
    """

    conversation_id: str
    agents: list[AgoraAgent]
    bus: MessageBus = field(default_factory=MessageBus)
    limits: RunnerLimits = field(default_factory=RunnerLimits)
    sender_id: str = "speaker_caller"
    runner_factory: RunnerFactory = field(default=default_runner_factory)

    @property
    def kind(self) -> str:
        return "agora"

    @property
    def speaker_id(self) -> str:
        return self.conversation_id

    def send_message(self, msg: Message) -> Response:
        runner = self.runner_factory(self.bus, self.limits)
        opening = PeerMessage(
            sender_id=self.sender_id,
            recipients=["*"],
            content=msg.payload,
        )
        result = runner.run(self.agents, opening)
        content = self._synthesise_conversation(result.final_messages) or "[empty]"
        return Response(
            id=msg.id,
            content=content,
            kind=self.kind,
            speaker_id=self.speaker_id,
            sub_responses=None,  # conversation history is summarised, not exposed
            metadata={
                "turns": result.turns,
                "elapsed_sec": result.elapsed_sec,
                "terminated_reason": result.terminated_reason,
                "agent_count": len(self.agents),
            },
        )

    @staticmethod
    def _synthesise_conversation(messages: list[Any]) -> str:
        if not messages:
            return ""
        return "\n".join(f"{m.sender_id}: {m.content}" for m in messages)
