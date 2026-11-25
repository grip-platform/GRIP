"""
Broly - Recursive Meta-Agent with Fractal Architecture

Concept: a collaborator (compressed information language specification)
Implementation: GRIP framework

A recursive meta-agent that scales from single-session reasoning to
multi-agent swarms via Claude Code's Agent Teams feature.

Power Levels:
    THREAD  - Single session, CoT reasoning, STM only
    FORK    - Task tool subagents (2-3), CoT+ToT, STM+LTM
    CLUSTER - Agent Team swarm (5-7), +GoT, all 4 memory types
    PIPELINE - Agent Team pipeline (7-10), all frameworks, nested depth
    MESH    - Agent Team council (10-15), meta-reasoning, evolution

Usage:
    from broly import BrolyOrchestrator, PowerLevel

    orch = BrolyOrchestrator(power_level="cluster")
    result = orch.execute("Analyze codebase security")
"""

from .power_levels import (
    PowerLevel,
    can_access_memory,
    can_use_reasoning,
    get_spawn_config,
    get_available_frameworks,
    resolve_power_level,
    MAX_RECURSION_DEPTH,
)
from .memory import BrolyMemory, MemoryAccessDenied
from .reasoning import ReasoningRouter, ReasoningAccessDenied
from .orchestrator import BrolyOrchestrator
from .consolidator import MemoryConsolidator
from .stm import ShortTermMemory
from .ltm import LongTermMemory
from .episodic import EpisodicMemory
from .semantic import SemanticMemory
from .team_config import build_team_config, build_task_decomposition

__version__ = "1.0.0"

__all__ = [
    # Core
    "BrolyOrchestrator",
    "BrolyMemory",
    "ReasoningRouter",
    "MemoryConsolidator",
    # Power Levels
    "PowerLevel",
    "can_access_memory",
    "can_use_reasoning",
    "get_spawn_config",
    "get_available_frameworks",
    "resolve_power_level",
    "MAX_RECURSION_DEPTH",
    # Memory Types
    "ShortTermMemory",
    "LongTermMemory",
    "EpisodicMemory",
    "SemanticMemory",
    # Team Config
    "build_team_config",
    "build_task_decomposition",
    # Exceptions
    "MemoryAccessDenied",
    "ReasoningAccessDenied",
]
