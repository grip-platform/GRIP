"""AGORA — multi-LLM peer reasoning protocol.

Public API surface for callers. Implementation modules import from each
other internally; external code imports from this package root.
"""
from lib.agora.agent import AgoraAgent
from lib.agora.bus import MessageBus
from lib.agora.falsifiers import (
    FalsifierVerdict,
    check_babel,
    check_consensus_collapse,
    run_all,
)
from lib.agora.message import SYSTEM_SENDER, Decision, Message, Response
from lib.agora.runner import ConversationRunner, RunnerLimits, RunResult
from lib.agora.supervisors import Intervention, falsifier_gate
from lib.agora.telemetry import TelemetryAggregate, TelemetryRecord

__all__ = [
    "AgoraAgent",
    "ConversationRunner",
    "Decision",
    "FalsifierVerdict",
    "Intervention",
    "Message",
    "MessageBus",
    "Response",
    "RunResult",
    "RunnerLimits",
    "SYSTEM_SENDER",
    "TelemetryAggregate",
    "TelemetryRecord",
    "check_babel",
    "check_consensus_collapse",
    "falsifier_gate",
    "run_all",
]
