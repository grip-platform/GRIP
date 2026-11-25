"""
Broly Orchestrator - Core execute/spawn/merge loop.

Concept: a collaborator (compressed information language specification)
Implementation: GRIP framework

The BrolyOrchestrator is the main entry point for Broly invocations.
It coordinates:
    1. Task assessment (complexity, domain)
    2. Power level resolution (auto or explicit)
    3. Memory retrieval (context enrichment)
    4. Reasoning framework selection
    5. Execution via Task tool (THREAD/FORK) or Agent Teams (CLUSTER+)
    6. Result merging
    7. Post-execution reflection and memory consolidation

Usage:
    orchestrator = BrolyOrchestrator(power_level="auto")
    result = orchestrator.execute("Analyze codebase security")
"""

import json
import logging
import os
from typing import Any, Dict, List, Optional

from pathlib import Path

logger = logging.getLogger(__name__)

from .power_levels import (
    PowerLevel,
    resolve_power_level,
    get_spawn_config,
    get_available_frameworks,
    should_extend_context,
    requires_extended_context,
    max_power_level_for_budget,
    MAX_RECURSION_DEPTH,
)
from .memory import BrolyMemory
from .consolidator import MemoryConsolidator
from .reasoning import ReasoningRouter
from .team_config import build_team_config, build_task_decomposition


class BrolyOrchestrator:
    """
    Main orchestrator for the Broly recursive meta-agent.

    Coordinates memory, reasoning, and multi-agent execution
    across power levels.
    """

    def __init__(
        self,
        power_level: str = "auto",
        project_path: Optional[str] = None,
        reasoning_hint: Optional[str] = None,
        escalation_config: Optional[Dict[str, Any]] = None,
    ):
        self._level = self._resolve_level(power_level)
        self._reasoning_hint = reasoning_hint
        self._escalation_config = escalation_config
        self._memory = BrolyMemory(power_level=self._level)
        self._reasoning = ReasoningRouter(power_level=self._level)
        self._consolidator = MemoryConsolidator(
            stm=self._memory._stm,
            ltm=self._memory._ltm,
            on_promote=self._on_memory_promoted,
        ) if self._memory._ltm else None
        self._execution_log: List[Dict[str, Any]] = []
        self._agent_teams_available = self._check_agent_teams()

    @classmethod
    def from_mode(cls, mode_config: Dict[str, Any]) -> "BrolyOrchestrator":
        """
        Create orchestrator from mode-derived broly config.

        Args:
            mode_config: Dict with keys from mode YAML broly section:
                - enabled: bool
                - default_level: str (thread/fork/cluster/pipeline/mesh)
                - team_pattern: str or null
                - reasoning_hint: str (cot/tot/got/bot/selfask/react)
                - escalation: dict with 'to' and 'signals' or null

        Returns:
            Configured BrolyOrchestrator instance

        Raises:
            ValueError: If broly is disabled in mode config
        """
        if not mode_config.get("enabled", False):
            raise ValueError("Broly is disabled in this mode config")

        level = mode_config.get("default_level", "thread")
        hint = mode_config.get("reasoning_hint")
        escalation = mode_config.get("escalation")

        return cls(
            power_level=level,
            reasoning_hint=hint,
            escalation_config=escalation,
        )

    def assess_escalation(self, task: str) -> Optional[PowerLevel]:
        """
        Check if escalation is warranted based on mode escalation config.

        Compares task description against escalation signals defined
        in the mode's broly config.

        Args:
            task: Task description to assess

        Returns:
            Escalated PowerLevel if signals match, None otherwise
        """
        if not self._escalation_config:
            return None

        target_level = self._escalation_config.get("to")
        signals = self._escalation_config.get("signals", [])

        if not target_level or not signals:
            return None

        task_lower = task.lower()
        for signal in signals:
            # Check if signal keywords appear in task description
            signal_words = [w.strip().lower() for w in signal.split() if len(w.strip()) > 3]
            match_count = sum(1 for w in signal_words if w in task_lower)
            # If more than half of significant words match, escalate
            if signal_words and match_count >= len(signal_words) * 0.5:
                try:
                    return resolve_power_level(target_level)
                except ValueError:
                    return None

        return None

    def _resolve_level(self, level_str: str) -> PowerLevel:
        """Resolve power level, with auto-detection via BAR."""
        if level_str.lower() == "auto":
            return self._auto_detect_level()
        return resolve_power_level(level_str)

    def _auto_detect_level(self) -> PowerLevel:
        """Use Broly Adaptive Router for intelligent level selection."""
        try:
            from prompt_router import classify_prompt
            decision = classify_prompt("", context={
                "mode_config": self._escalation_config,
            })
            self._routing_decision = decision
            return resolve_power_level(decision.power_level)
        except Exception:
            self._routing_decision = None
            return PowerLevel.THREAD

    def _check_agent_teams(self) -> bool:
        """Check if Agent Teams feature is available."""
        return os.environ.get("CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS") == "1"

    @property
    def power_level(self) -> PowerLevel:
        return self._level

    @property
    def memory(self) -> BrolyMemory:
        return self._memory

    # =========================================================================
    # MAIN EXECUTION
    # =========================================================================

    def _check_context_requirements(self) -> str | None:
        """Check if current context window meets power level requirements.

        Returns error/warning message, or None if OK.
        """
        model_state_file = Path.home() / ".claude" / ".model-state.json"
        is_extended = False
        try:
            if model_state_file.exists():
                import json as _json
                state = _json.loads(model_state_file.read_text())
                is_extended = state.get("extended", False)
        except Exception:
            pass

        if requires_extended_context(self._level) and not is_extended:
            return (
                f"[CONTEXT REQUIRED] {self._level.value.upper()} requires Sonnet 1M context. "
                f"Run /extend-context before using this power level."
            )

        if should_extend_context(self._level) and not is_extended:
            return (
                f"[CONTEXT RECOMMENDED] {self._level.value.upper()} recommends Sonnet 1M context. "
                f"Consider /extend-context for optimal performance. Proceeding with caution."
            )

        return None

    def execute(
        self,
        task: str,
        context: Optional[str] = None,
        framework: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Execute a task through the Broly pipeline.

        Args:
            task: Task description
            context: Optional additional context
            framework: Force a specific reasoning framework (auto if None)

        Returns:
            Execution result dict with:
                - result: The output/answer
                - framework: Reasoning framework used
                - power_level: Power level used
                - memory_hits: Relevant memories found
                - agents_spawned: Number of agents used
        """
        # Reset per-execute state so multi-invoke scenarios re-run BAR
        self._bar_rerouted = False

        # Step 0: Budget-aware power level cap
        try:
            from lib.token_budget import TokenBudget
            _budget = TokenBudget.detect()
            if _budget.mode == "oracle":
                # Oracle mode: no Broly at all — return immediately
                return {
                    "result": f"[budget:oracle] Broly disabled. Direct single-shot for: {task}",
                    "framework": "none",
                    "power_level": "off",
                    "mechanism": "budget_gated",
                    "memory_context": None,
                    "prompt": "",
                    "agents_spawned": 0,
                    "memories_promoted": 0,
                }
            cap_level = max_power_level_for_budget(_budget)
            all_levels = list(PowerLevel)
            cur_idx = all_levels.index(self._level)
            cap_idx = all_levels.index(cap_level)
            if cur_idx > cap_idx:
                logger.info(
                    "Broly degraded from %s to %s (budget: %s)",
                    self._level.value, cap_level.value, _budget.mode,
                )
                self._level = cap_level
                self._reasoning = ReasoningRouter(power_level=self._level)
        except (ImportError, Exception) as e:
            logger.debug("Budget cap unavailable: %s", e)

        # Step 1: Memory retrieval for context enrichment
        memory_context = self._retrieve_memory_context(task)

        # Step 1b: Check for escalation based on mode config
        escalated_level = self.assess_escalation(task)
        if escalated_level and escalated_level.value != self._level.value:
            self._level = escalated_level
            # Re-init reasoning router for new level
            self._reasoning = ReasoningRouter(power_level=self._level)

        # Step 1b2: BAR re-routing with actual task text
        # Initial _auto_detect_level had empty text; now we have the real task.
        # Guard: only re-route once per execute() call, skip if framework forced.
        if task and not framework and not getattr(self, "_bar_rerouted", False):
            self._bar_rerouted = True
            try:
                from prompt_router import classify_prompt
                bar_decision = classify_prompt(task, context={
                    "mode_config": self._escalation_config,
                })
                bar_level = resolve_power_level(bar_decision.power_level)
                # BAR can upgrade but not downgrade from explicit escalation
                if bar_level.value != self._level.value:
                    bar_idx = list(PowerLevel).index(bar_level)
                    cur_idx = list(PowerLevel).index(self._level)
                    if bar_idx > cur_idx:
                        self._level = bar_level
                        self._reasoning = ReasoningRouter(power_level=self._level)
                        self._routing_decision = bar_decision
            except Exception as e:  # noqa: BLE001
                # BAR routing unavailable — using current power level as-is
                logger.warning("BAR routing skipped: %s", e)
                self._bar_routing_error = str(e)

        # Step 1c: Check context requirements
        context_check = self._check_context_requirements()
        if context_check and requires_extended_context(self._level):
            return {
                "result": context_check,
                "framework": "none",
                "power_level": self._level.value,
                "mechanism": "blocked",
                "memory_context": None,
                "prompt": "",
                "agents_spawned": 0,
                "memories_promoted": 0,
            }

        # Step 2: Select reasoning framework (prefer mode hint if available)
        selected_framework = framework or self._reasoning_hint or self._reasoning.select_framework(task)
        # Validate that the hinted framework is available at current level
        available = get_available_frameworks(self._level)
        if selected_framework not in available:
            selected_framework = self._reasoning.select_framework(task)

        # Step 3: Generate reasoning prompt
        prompt = self._reasoning.generate_prompt(
            framework=selected_framework,
            task=task,
            context=context,
            memory_context=memory_context,
        )

        # Step 4: Execute based on power level
        spawn_config = get_spawn_config(self._level)
        mechanism = spawn_config["mechanism"]

        # Degrade gracefully: if teams unavailable, fall back to task tool
        if mechanism == "team" and not self._agent_teams_available:
            mechanism = "task"

        result = self._execute_by_mechanism(
            mechanism=mechanism,
            task=task,
            prompt=prompt,
            framework=selected_framework,
        )

        # Step 5: Store execution in STM
        self._memory.store(
            key=f"execution:{task[:50]}",
            value={
                "task": task,
                "framework": selected_framework,
                "result_preview": str(result.get("result", ""))[:200],
            },
            memory_type="stm",
        )

        # Step 6: Post-execution consolidation
        if self._consolidator:
            promoted = self._consolidator.consolidate()
            result["memories_promoted"] = len(promoted)

        # Step 7: Log execution
        log_entry = {
            "task": task,
            "power_level": self._level.value,
            "framework": selected_framework,
            "mechanism": mechanism,
            "memory_hits": len(memory_context) if memory_context else 0,
        }
        self._execution_log.append(log_entry)

        # Connection 2: Export execution summary for delivery reports
        execution_summary = self._build_execution_summary(
            task, selected_framework, mechanism, result
        )

        # Connection 3: Feed BAR routing decision back for learning
        self._feed_bar_routing_feedback(task, selected_framework, result)

        # Connection 4: Record team pattern metrics for genome evolution
        if mechanism == "team":
            self._record_team_pattern_metrics(task, result)

        return {
            "result": result.get("result", ""),
            "framework": selected_framework,
            "power_level": self._level.value,
            "mechanism": mechanism,
            "memory_context": memory_context,
            "prompt": prompt,
            "agents_spawned": result.get("agents_spawned", 1),
            "memories_promoted": result.get("memories_promoted", 0),
            "execution_summary": execution_summary,
            "bar_routing_skipped": getattr(self, "_bar_routing_error", None),
        }

    # =========================================================================
    # CROSS-SUBSYSTEM CONNECTIONS (#65)
    # =========================================================================

    def _on_memory_promoted(self, key: str, record: Dict[str, Any]) -> None:
        """Connection 5 (callback): Genome fitness signal on STM→LTM promotion.

        When memory consolidation promotes an STM entry, this signals the genome
        that the memory strategies governing this session were effective.
        """
        try:
            lib_dir = str(Path.home() / ".claude" / "lib")
            import sys
            if lib_dir not in sys.path:
                sys.path.insert(0, lib_dir)
            from converge_dna_core import register_gene_invocation
            register_gene_invocation("broly-memory-consolidation")
        except (ImportError, Exception) as e:
            logger.debug("Genome memory promotion signal unavailable: %s", e)


    def _build_execution_summary(
        self,
        task: str,
        framework: str,
        mechanism: str,
        result: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Connection 2: Build execution summary for delivery reports.

        Produces a structured summary that downstream consumers
        (delivery_report.py, deliverable_generator.py) can use.
        """
        return {
            "task": task[:200],
            "power_level": self._level.value,
            "framework": framework,
            "mechanism": mechanism,
            "agents_spawned": result.get("agents_spawned", 1),
            "execution_type": result.get("execution_type", "unknown"),
            "has_result": bool(result.get("result")),
        }

    def _feed_bar_routing_feedback(
        self,
        task: str,
        framework: str,
        result: Dict[str, Any],
    ) -> None:
        """Connection 3: Feed BAR routing decision back for learning.

        Closes the feedback loop so BAR can learn from execution outcomes.
        The routing_decision from auto_detect_level gets paired with
        execution results to improve future routing.
        """
        routing_decision = getattr(self, "_routing_decision", None)
        if not routing_decision:
            return

        try:
            lib_dir = str(Path.home() / ".claude" / "lib")
            import sys
            if lib_dir not in sys.path:
                sys.path.insert(0, lib_dir)
            from prompt_router import record_routing_feedback
            record_routing_feedback(
                decision=routing_decision,
                actual_level=self._level.value,
                framework_used=framework,
                outcome_success=bool(result.get("result")),
            )
        except (ImportError, AttributeError, Exception) as e:
            logger.debug("BAR feedback unavailable: %s", e)

    def _record_team_pattern_metrics(
        self,
        task: str,
        result: Dict[str, Any],
    ) -> None:
        """Connection 4: Record team pattern metrics for genome evolution.

        When Agent Teams execute, capture which pattern (swarm/pipeline/council)
        was used and whether it produced results. This feeds into genome
        gene fitness for team-structure selection.
        """
        try:
            lib_dir = str(Path.home() / ".claude" / "lib")
            import sys
            if lib_dir not in sys.path:
                sys.path.insert(0, lib_dir)
            from converge_dna_core import register_gene_invocation
            # Register the team pattern as an invoked gene
            pattern_gene = f"broly-team-{self._level.value}"
            register_gene_invocation(pattern_gene)
        except (ImportError, Exception) as e:
            logger.debug("Genome team pattern tracking unavailable: %s", e)

    # =========================================================================
    # MEMORY RETRIEVAL
    # =========================================================================

    def _retrieve_memory_context(self, task: str) -> Optional[str]:
        """Retrieve relevant memory context for task enrichment."""
        all_memories = self._memory.recall_all(task, limit_per_type=3)

        if not all_memories:
            return None

        parts = []

        # STM (exact match)
        if "stm" in all_memories and all_memories["stm"] is not None:
            parts.append(f"**Recent:** {str(all_memories['stm'])[:200]}")

        # LTM
        if "ltm" in all_memories:
            for hit in all_memories["ltm"][:2]:
                parts.append(f"**Long-term:** {hit.get('content', '')[:200]}")

        # Episodic
        if "episodic" in all_memories:
            for hit in all_memories["episodic"][:2]:
                parts.append(f"**Past session:** {hit.get('content', '')[:200]}")

        # Semantic
        if "semantic" in all_memories:
            for hit in all_memories["semantic"][:2]:
                parts.append(f"**Related concept:** {hit.get('content', '')[:200]}")

        return "\n".join(parts) if parts else None

    # =========================================================================
    # EXECUTION MECHANISMS
    # =========================================================================

    def _execute_by_mechanism(
        self,
        mechanism: str,
        task: str,
        prompt: str,
        framework: str,
    ) -> Dict[str, Any]:
        """
        Execute via the appropriate mechanism.

        Returns a dict with 'result' and 'agents_spawned'.
        """
        if mechanism == "none":
            return self._execute_single(prompt)

        if mechanism == "task":
            return self._execute_via_task_tool(task, prompt, framework)

        if mechanism == "team":
            return self._execute_via_agent_team(task, prompt, framework)

        return self._execute_single(prompt)

    def _execute_single(self, prompt: str) -> Dict[str, Any]:
        """Single-agent execution (THREAD level)."""
        return {
            "result": prompt,
            "agents_spawned": 1,
            "execution_type": "single",
        }

    def _execute_via_task_tool(
        self,
        task: str,
        prompt: str,
        framework: str,
    ) -> Dict[str, Any]:
        """
        Execute via Task tool subagents (FORK level).

        Generates Task tool invocation instructions for the calling agent.
        The orchestrator doesn't call Task directly - it returns instructions
        that the Claude Code agent interprets.
        """
        spawn = get_spawn_config(self._level)
        max_agents = spawn["max_agents"]

        # Generate subagent instructions
        subagents = []
        if framework == "tot":
            # ToT: parallel branches
            branches = ["Approach A (conservative)", "Approach B (innovative)", "Approach C (hybrid)"]
            for i, branch in enumerate(branches[:max_agents]):
                subagents.append({
                    "type": "general-purpose",
                    "prompt": f"{prompt}\n\nFocus on: {branch}",
                    "description": f"ToT branch {i+1}",
                })
        else:
            # Default: research + implementation split
            subagents.append({
                "type": "Explore",
                "prompt": f"Research phase for: {task}\n\n{prompt}",
                "description": "Research phase",
            })
            if max_agents > 1:
                subagents.append({
                    "type": "general-purpose",
                    "prompt": f"Implementation phase for: {task}\n\nBased on research, execute.",
                    "description": "Implementation phase",
                })

        return {
            "result": json.dumps({
                "type": "task_tool_instructions",
                "subagents": subagents,
                "merge_strategy": "sequential" if framework != "tot" else "best_of",
            }, indent=2),
            "agents_spawned": len(subagents),
            "execution_type": "task_tool",
        }

    def _execute_via_agent_team(
        self,
        task: str,
        prompt: str,
        framework: str,
    ) -> Dict[str, Any]:
        """
        Execute via Agent Teams (CLUSTER+ levels).

        Generates team configuration and task decomposition that the
        Claude Code agent uses to spawn an Agent Team.
        """
        team_config = build_team_config(self._level, task)

        if not team_config:
            # Fallback to task tool
            return self._execute_via_task_tool(task, prompt, framework)

        subtasks = build_task_decomposition(
            pattern=team_config["pattern"],
            task=task,
            roles=team_config["roles"],
        )

        return {
            "result": json.dumps({
                "type": "agent_team_instructions",
                "team": team_config,
                "subtasks": subtasks,
                "prompt": prompt,
                "merge_strategy": self._get_merge_strategy(team_config["pattern"]),
            }, indent=2),
            "agents_spawned": len(subtasks),
            "execution_type": "agent_team",
        }

    def _get_merge_strategy(self, pattern: str) -> str:
        """Get merge strategy for a team pattern."""
        strategies = {
            "swarm": "concatenate",
            "pipeline": "sequential",
            "council": "vote_and_synthesize",
        }
        return strategies.get(pattern, "concatenate")

    # =========================================================================
    # STATUS AND STATS
    # =========================================================================

    def get_status(self) -> Dict[str, Any]:
        """Get current orchestrator status."""
        return {
            "power_level": self._level.value,
            "reasoning_hint": self._reasoning_hint,
            "escalation_config": self._escalation_config,
            "agent_teams_available": self._agent_teams_available,
            "available_frameworks": get_available_frameworks(self._level),
            "spawn_config": get_spawn_config(self._level),
            "memory_stats": self._memory.get_stats(),
            "execution_count": len(self._execution_log),
            "consolidator": self._consolidator.get_stats() if self._consolidator else None,
        }

    def get_execution_log(self) -> List[Dict[str, Any]]:
        """Get the execution log for this session."""
        return self._execution_log


# =========================================================================
# CLI INTERFACE
# =========================================================================

def main():
    """CLI interface for Broly orchestrator."""
    import argparse

    parser = argparse.ArgumentParser(description="Broly Recursive Meta-Agent")
    subparsers = parser.add_subparsers(dest="command")

    # Execute
    exec_parser = subparsers.add_parser("execute", help="Execute a task")
    exec_parser.add_argument("task", help="Task description")
    exec_parser.add_argument("--level", "-l", default="auto", help="Power level")
    exec_parser.add_argument("--framework", "-f", help="Force reasoning framework")

    # Status
    subparsers.add_parser("status", help="Show orchestrator status")

    # Assess
    assess_parser = subparsers.add_parser("assess", help="Assess task without executing")
    assess_parser.add_argument("task", help="Task description")
    assess_parser.add_argument("--level", "-l", default="auto", help="Power level")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    if args.command == "execute":
        orch = BrolyOrchestrator(power_level=args.level)
        result = orch.execute(args.task, framework=args.framework)
        print(json.dumps(result, indent=2, default=str))

    elif args.command == "status":
        orch = BrolyOrchestrator()
        print(json.dumps(orch.get_status(), indent=2, default=str))

    elif args.command == "assess":
        orch = BrolyOrchestrator(power_level=args.level)
        router = orch._reasoning
        framework = router.select_framework(args.task)
        memory_context = orch._retrieve_memory_context(args.task)
        print(json.dumps({
            "task": args.task,
            "power_level": orch.power_level.value,
            "suggested_framework": framework,
            "available_frameworks": get_available_frameworks(orch.power_level),
            "spawn_config": get_spawn_config(orch.power_level),
            "memory_hits": len(memory_context) if memory_context else 0,
        }, indent=2))


if __name__ == "__main__":
    main()
