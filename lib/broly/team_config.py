"""
Broly Team Configuration - Agent Team roles, patterns, and CLAUDE.md injection.

Concept: a collaborator (compressed information language specification)
Implementation: GRIP framework

Configures Claude Code Agent Teams for cluster+ power levels:
    - Team name and pattern (swarm/pipeline/council)
    - Teammate roles and models
    - Shared CLAUDE.md context for teammates
    - Task decomposition templates
"""

from typing import Any, Dict, List, Optional

from .power_levels import PowerLevel, get_spawn_config, get_team_roles


def build_team_config(
    power_level: PowerLevel,
    task: str,
    custom_roles: Optional[List[Dict[str, str]]] = None,
) -> Optional[Dict[str, Any]]:
    """
    Build Agent Team configuration for a power level.

    Args:
        power_level: Current Broly power level
        task: Task description for context
        custom_roles: Optional override for default roles

    Returns:
        Team config dict or None if level doesn't use teams
    """
    spawn = get_spawn_config(power_level)

    if spawn["mechanism"] != "team":
        return None

    roles = custom_roles or get_team_roles(power_level)
    pattern = spawn["team_pattern"]

    return {
        "team_name": f"broly-{pattern}",
        "pattern": pattern,
        "max_agents": spawn["max_agents"],
        "roles": roles,
        "task": task,
        "claude_md": _generate_teammate_claude_md(pattern, task),
    }


def _generate_teammate_claude_md(pattern: str, task: str) -> str:
    """
    Generate CLAUDE.md content injected into each teammate's context.

    This gives teammates awareness of the Broly coordination pattern
    and their role within it.
    """
    base = f"""# Broly Team Member

You are part of a Broly meta-agent team (pattern: {pattern}).

## Task
{task}

## Coordination Rules
1. Use the shared task list to track your work
2. Mark tasks as in_progress when starting, completed when done
3. Communicate findings via task descriptions
4. Do NOT duplicate work - check what others have done first
5. Follow all GRIP safety rules (they're inherited from the lead)

"""

    if pattern == "swarm":
        base += """## Swarm Pattern
- Work on independent subtasks in parallel
- Each teammate owns a specific aspect
- Report results via task completion
- Lead will merge all results
"""

    elif pattern == "pipeline":
        base += """## Pipeline Pattern
- Tasks flow through stages sequentially
- Wait for upstream dependencies before starting
- Your output becomes input for the next stage
- Quality gates between stages
"""

    elif pattern == "council":
        base += """## Council Pattern
- Each member provides an independent analysis from their specialty
- After analysis, members review each other's positions
- Vote on the best approach with reasoning
- Integrator synthesizes the final decision
- Dissenting opinions are recorded, not suppressed
"""

    return base


def build_task_decomposition(
    pattern: str,
    task: str,
    roles: List[Dict[str, str]],
) -> List[Dict[str, Any]]:
    """
    Decompose a task into subtasks based on team pattern and roles.

    Returns list of task dicts ready for the shared task list.
    """
    subtasks = []

    if pattern == "swarm":
        # Each role gets an independent subtask
        for role in roles:
            subtasks.append({
                "subject": f"[{role['name']}] Analyze: {task[:60]}",
                "description": (
                    f"As the {role['name']} ({role['specialty']}), "
                    f"analyze the following task from your perspective:\n\n{task}"
                ),
                "owner": role["name"],
                "model": role.get("model", "sonnet"),
            })

    elif pattern == "pipeline":
        # Sequential stages with dependencies
        prev_id = None
        for i, role in enumerate(roles):
            stage_task = {
                "subject": f"Stage {i+1} [{role['name']}]: {task[:50]}",
                "description": (
                    f"Pipeline stage {i+1}. As {role['name']} ({role['specialty']}), "
                    f"process the task:\n\n{task}\n\n"
                    f"Build on the output of previous stages."
                ),
                "owner": role["name"],
                "model": role.get("model", "sonnet"),
                "stage": i + 1,
            }
            if prev_id is not None:
                stage_task["blocked_by"] = prev_id
            subtasks.append(stage_task)
            prev_id = f"stage_{i+1}"

    elif pattern == "council":
        # Independent analysis + review + vote
        # Phase 1: Independent analysis
        for role in roles:
            if role["name"] == "integrator":
                continue
            subtasks.append({
                "subject": f"[{role['name']}] Independent analysis: {task[:50]}",
                "description": (
                    f"As the {role['name']} ({role['specialty']}), "
                    f"provide your independent analysis of:\n\n{task}\n\n"
                    f"Include: assessment, recommendation, confidence (1-10), "
                    f"and key risks from your perspective."
                ),
                "owner": role["name"],
                "model": role.get("model", "sonnet"),
                "phase": "analysis",
            })

        # Phase 2: Integration (depends on all analyses)
        integrator = next((r for r in roles if r["name"] == "integrator"), roles[-1])
        subtasks.append({
            "subject": f"[{integrator['name']}] Synthesize council decision",
            "description": (
                f"As the {integrator['name']}, synthesize all council member "
                f"analyses into a unified decision.\n\n"
                f"1. Summarize each member's position\n"
                f"2. Identify points of agreement and disagreement\n"
                f"3. Weigh arguments by confidence and evidence\n"
                f"4. Produce final recommendation with dissent notes"
            ),
            "owner": integrator["name"],
            "model": integrator.get("model", "opus"),
            "phase": "integration",
            "depends_on_phase": "analysis",
        })

    return subtasks
