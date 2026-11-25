"""
Broly Power Levels - Configuration enum for recursive meta-agent scaling.

Concept: a collaborator (compressed information language specification)
Implementation: GRIP framework

Each power level controls:
    - Spawn mechanism (Task tool vs Agent Teams)
    - Max concurrent agents
    - Available reasoning frameworks
    - Memory type access
    - Team pattern (swarm/pipeline/council)
    - Token budget multiplier
"""

from enum import Enum
from typing import Dict, List, Optional, Set, Tuple, Any


class PowerLevel(Enum):
    """Power levels controlling agent scaling behavior."""
    THREAD = "thread"
    FORK = "fork"
    CLUSTER = "cluster"
    PIPELINE = "pipeline"
    MESH = "mesh"


# Memory types available at each power level
MEMORY_ACCESS: Dict[PowerLevel, Set[str]] = {
    PowerLevel.THREAD: {"stm"},
    PowerLevel.FORK: {"stm", "ltm"},
    PowerLevel.CLUSTER: {"stm", "ltm", "episodic", "semantic"},
    PowerLevel.PIPELINE: {"stm", "ltm", "episodic", "semantic"},
    PowerLevel.MESH: {"stm", "ltm", "episodic", "semantic"},
}

# Reasoning frameworks available at each power level
REASONING_ACCESS: Dict[PowerLevel, List[str]] = {
    PowerLevel.THREAD: ["cot"],
    PowerLevel.FORK: ["cot", "tot"],
    PowerLevel.CLUSTER: ["cot", "tot", "got"],
    PowerLevel.PIPELINE: ["cot", "tot", "got", "bot", "selfask", "react"],
    PowerLevel.MESH: ["cot", "tot", "got", "bot", "selfask", "react", "reflexion"],
}

# Spawn configuration per power level
SPAWN_CONFIG: Dict[PowerLevel, Dict[str, Any]] = {
    PowerLevel.THREAD: {
        "mechanism": "none",
        "max_agents": 1,
        "team_pattern": None,
        "token_multiplier": 1.0,
        "recommended_context": 1000000,
        "requires_extended": False,
    },
    PowerLevel.FORK: {
        "mechanism": "task",
        "max_agents": 3,
        "team_pattern": None,
        "token_multiplier": 2.0,
        "recommended_context": 1000000,
        "requires_extended": False,
    },
    PowerLevel.CLUSTER: {
        "mechanism": "team",
        "max_agents": 7,
        "team_pattern": "swarm",
        "token_multiplier": 5.0,
        "recommended_context": 1000000,
        "requires_extended": False,
    },
    PowerLevel.PIPELINE: {
        "mechanism": "team",
        "max_agents": 10,
        "team_pattern": "pipeline",
        "token_multiplier": 5.0,
        "recommended_context": 1000000,
        "requires_extended": True,
    },
    PowerLevel.MESH: {
        "mechanism": "team",
        "max_agents": 15,
        "team_pattern": "council",
        "token_multiplier": 5.0,
        "recommended_context": 1000000,
        "requires_extended": True,
    },
}

# Max recursion depth (Team lead -> teammate -> Task subagent)
MAX_RECURSION_DEPTH = 3


def can_access_memory(level: PowerLevel, memory_type: str) -> bool:
    """Check if a power level grants access to a memory type."""
    return memory_type in MEMORY_ACCESS.get(level, set())


def can_use_reasoning(level: PowerLevel, framework: str) -> bool:
    """Check if a power level grants access to a reasoning framework."""
    return framework in REASONING_ACCESS.get(level, [])


def get_spawn_config(level: PowerLevel) -> Dict[str, Any]:
    """Get spawn configuration for a power level."""
    return SPAWN_CONFIG.get(level, SPAWN_CONFIG[PowerLevel.THREAD])


def resolve_power_level(name: str) -> PowerLevel:
    """Resolve a power level from string name (case-insensitive)."""
    name_upper = name.upper().replace("-", "").replace("_", "")
    # Handle 'auto' as THREAD
    if name_upper == "AUTO":
        return PowerLevel.THREAD
    # Backward-compatible aliases for old names
    ALIASES = {
        "BASE": "THREAD", "SSJ": "FORK", "SSJ2": "CLUSTER",
        "SSJ3": "PIPELINE", "LSSJ": "MESH",
    }
    if name_upper in ALIASES:
        name_upper = ALIASES[name_upper]
    for level in PowerLevel:
        if level.value.upper() == name_upper or level.name == name_upper:
            return level
    raise ValueError(f"Unknown power level: {name}. Valid: {[l.value for l in PowerLevel]}")


def get_available_frameworks(level: PowerLevel) -> List[str]:
    """Get list of reasoning frameworks available at a power level."""
    return REASONING_ACCESS.get(level, ["cot"])


def get_team_roles(
    level: PowerLevel,
    budget_context: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, str]]:
    """
    Get default teammate roles for team-based power levels.

    Args:
        level: Power level to get roles for.
        budget_context: Optional dict with budget state for model downgrades.
            Expected structure: {"weekly_spend_pct": float}
            Thresholds: >=50% downgrades light roles to haiku,
                        >=80% also downgrades opus to sonnet,
                        >=90% downgrades ALL roles to haiku.
            If None or missing weekly_spend_pct, no downgrades applied.

    Returns:
        List of role dicts, each with keys: name (str), specialty (str), model (str).
        Empty list if power level does not use team mechanism.
    """
    config = get_spawn_config(level)
    if config["mechanism"] != "team":
        return []

    pattern = config["team_pattern"]

    if pattern == "swarm":
        roles = [
            {"name": "analyst", "specialty": "deep analysis and research", "model": "sonnet"},
            {"name": "implementer", "specialty": "code implementation", "model": "sonnet"},
            {"name": "reviewer", "specialty": "code review and testing", "model": "sonnet"},
            {"name": "explorer", "specialty": "codebase exploration", "model": "haiku"},
            {"name": "documenter", "specialty": "documentation and summaries", "model": "haiku"},
        ]
    elif pattern == "pipeline":
        roles = [
            {"name": "researcher", "specialty": "research and information gathering", "model": "sonnet"},
            {"name": "architect", "specialty": "system design and architecture", "model": "opus"},
            {"name": "implementer-1", "specialty": "primary implementation", "model": "sonnet"},
            {"name": "implementer-2", "specialty": "secondary implementation", "model": "sonnet"},
            {"name": "tester", "specialty": "testing and validation", "model": "sonnet"},
            {"name": "reviewer", "specialty": "final review and integration", "model": "sonnet"},
        ]
    elif pattern == "council":
        roles = [
            {"name": "pragmatist", "specialty": "practical engineering trade-offs", "model": "opus"},
            {"name": "theorist", "specialty": "algorithmic and theoretical analysis", "model": "opus"},
            {"name": "security-advisor", "specialty": "security and risk assessment", "model": "sonnet"},
            {"name": "ux-advocate", "specialty": "user experience and API design", "model": "sonnet"},
            {"name": "devil-advocate", "specialty": "challenging assumptions and finding flaws", "model": "opus"},
            {"name": "integrator", "specialty": "synthesizing viewpoints into decisions", "model": "opus"},
        ]
    else:
        return []

    # Budget-aware model downgrades
    if budget_context:
        pct = budget_context.get("weekly_spend_pct", 0.0)
        _LIGHT_ROLES = {"explorer", "documenter", "researcher", "tester"}
        for role in roles:
            if pct >= 90:
                role["model"] = "haiku"
            elif pct >= 80:
                if role["model"] == "opus":
                    role["model"] = "sonnet"
                if role["name"] in _LIGHT_ROLES:
                    role["model"] = "haiku"
            elif pct >= 50:
                if role["name"] in _LIGHT_ROLES:
                    role["model"] = "haiku"

    return roles


def max_power_level_for_budget(budget) -> PowerLevel:
    """Cap Broly power level based on budget tier.

    abundant: MESH (all levels available)
    moderate: FORK (max 3 agents)
    scarce:   THREAD (single agent, CoT only)
    oracle:   OFF — returns THREAD as minimum valid level

    Args:
        budget: TokenBudget instance with max_broly_level() method.
    """
    level_name = budget.max_broly_level()
    if level_name == "off":
        return PowerLevel.THREAD
    return resolve_power_level(level_name)


def should_extend_context(level: PowerLevel) -> bool:
    """Check if this power level recommends extended (1M) context."""
    config = get_spawn_config(level)
    return config.get("recommended_context", 1000000) > 1000000


def requires_extended_context(level: PowerLevel) -> bool:
    """Check if this power level requires extended (1M) context to run."""
    config = get_spawn_config(level)
    return config.get("requires_extended", False)
