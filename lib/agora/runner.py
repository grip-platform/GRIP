"""ConversationRunner — orchestrates rounds, enforces caps, fires supervisors.

Round-robin scheduling with skip-self. STOP votes do not post a message
(would rescind the vote on next post by sender); instead a synthetic
message with sender_id=SYSTEM_SENDER carries the stop signal so other
agents can respond in the next round.

W4: Supervisors are now wired in. Each REPLY/DEFER message is run through
the supervisors list; interventions are recorded for telemetry and can be
inspected post-run via RunResult.interventions.
"""
from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field

from lib.agora.agent import AgoraAgent
from lib.agora.bus import MessageBus
from lib.agora.message import SYSTEM_SENDER, Decision, Message, Response
from lib.agora.supervisors import Intervention

SupervisorFn = Callable[[Message], Intervention | None]


@dataclass
class RunnerLimits:
    turn_cap: int = 30
    time_cap_sec: float = 300.0
    quorum_ratio: float = 2 / 3


@dataclass
class RunResult:
    convo_id: str
    turns: int
    elapsed_sec: float
    terminated_reason: str
    final_messages: list[Message]
    interventions: list[Intervention] = field(default_factory=list)


class ConversationRunner:
    """Orchestrates an AGORA conversation. Stateless across runs."""

    def __init__(
        self,
        bus: MessageBus,
        limits: RunnerLimits | None = None,
        supervisors: list[SupervisorFn] | None = None,
    ) -> None:
        self.bus = bus
        self.limits = limits or RunnerLimits()
        self.supervisors = supervisors or []

    def run(self, agents: list[AgoraAgent], opening: Message) -> RunResult:
        convo_id = uuid.uuid4().hex[:8]
        agent_map = {a.agent_id: a for a in agents}
        self.bus.open(convo_id, list(agent_map.keys()))
        self.bus.post(convo_id, opening)
        interventions: list[Intervention] = []
        return self._loop(convo_id, agent_map, opening, interventions)

    def _loop(
        self,
        convo_id: str,
        agent_map: dict[str, AgoraAgent],
        opening: Message,
        interventions: list[Intervention],
    ) -> RunResult:
        start = time.monotonic()
        turns = 0
        last_message = opening
        agents_list = list(agent_map.values())
        idx = 0
        while turns < self.limits.turn_cap:
            if (time.monotonic() - start) > self.limits.time_cap_sec:
                return self._build_result(convo_id, turns, start, "time_cap", interventions)
            actor = self._pick_actor(agents_list, idx, last_message)
            idx += 1
            if actor is None:
                return self._build_result(convo_id, turns, start, "no_actor", interventions)
            response = actor.receive(last_message, history=self.bus.history(convo_id))
            turns += 1
            last_message = self._handle_response(
                convo_id, actor.agent_id, response, interventions,
            )
            if self.bus.quorum_reached(convo_id, self.limits.quorum_ratio):
                return self._build_result(convo_id, turns, start, "quorum_stop", interventions)
        return self._build_result(convo_id, turns, start, "turn_cap", interventions)

    @staticmethod
    def _pick_actor(
        agents_list: list[AgoraAgent], idx: int, last_message: Message,
    ) -> AgoraAgent | None:
        n = len(agents_list)
        for offset in range(n):
            candidate = agents_list[(idx + offset) % n]
            if candidate.agent_id != last_message.sender_id:
                return candidate
        return None

    def _handle_response(
        self,
        convo_id: str,
        actor_id: str,
        response: Response,
        interventions: list[Intervention],
    ) -> Message:
        if response.decision == Decision.STOP:
            self.bus.vote_stop(convo_id, actor_id)
            return Message(
                sender_id=SYSTEM_SENDER,
                recipients=["*"],
                content=f"[stop vote from {actor_id}] {response.stop_rationale or ''}",
            )
        new_msg = Message(
            sender_id=actor_id,
            recipients=["*"],
            content=response.content,
            intent=response.follow_up_intent,
        )
        self.bus.post(convo_id, new_msg)
        self._fire_supervisors(new_msg, interventions)
        return new_msg

    def _fire_supervisors(
        self, message: Message, interventions: list[Intervention],
    ) -> None:
        for supervisor in self.supervisors:
            intervention = supervisor(message)
            if intervention is not None:
                interventions.append(intervention)

    def _build_result(
        self,
        convo_id: str,
        turns: int,
        start: float,
        reason: str,
        interventions: list[Intervention],
    ) -> RunResult:
        return RunResult(
            convo_id=convo_id,
            turns=turns,
            elapsed_sec=time.monotonic() - start,
            terminated_reason=reason,
            final_messages=self.bus.history(convo_id),
            interventions=list(interventions),
        )
