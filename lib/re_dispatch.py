"""Recursive re-dispatch pattern for iterative agent refinement.

CLU iterator pattern applied to Claude Code agent delegation:
dispatch a worker, evaluate the result, refine and re-dispatch
until the criterion is met or max_depth is exhausted.

Works around CC's depth-1 subagent limit by using iterative
re-dispatch at depth 0 rather than nested agent spawning.

Usage:
    from re_dispatch import ReDispatcher, Criterion

    criterion = Criterion(
        description="All tests pass and PR created",
        check=lambda result: "PASS" in result and "PR #" in result
    )
    dispatcher = ReDispatcher(max_depth=5)
    result = dispatcher.run(
        task="Fix the auth bug in login.py",
        criterion=criterion,
        agent_type="direct-implementation"
    )

Origin: Gen 477, CLU iterator pattern (Liskov 1975).
Coordinator-convergence duality: this IS converge() for agent delegation.
"""

from dataclasses import dataclass, field
from typing import Callable, Optional


@dataclass
class Criterion:
    """Falsifiable evaluation criterion for re-dispatch loop."""

    description: str
    check: Callable[[str], bool]

    def evaluate(self, result: str) -> bool:
        """Return True if criterion is met."""
        return self.check(result)


@dataclass
class DispatchResult:
    """Result of a single dispatch iteration."""

    depth: int
    result: str
    criterion_met: bool
    refinement: Optional[str] = None


@dataclass
class ReDispatcher:
    """Iterative re-dispatch coordinator.

    Dispatches a task to a worker agent, evaluates the result,
    and refines + re-dispatches if the criterion is not met.

    This is the convergence kernel applied to agent delegation:
    - converge(): one function, many iterations (quality)
    - ReDispatcher: one agent task, many dispatches (completeness)
    """

    max_depth: int = 5
    history: list = field(default_factory=list)

    def build_prompt(self, task: str, depth: int, prior_results: list) -> str:
        """Build the dispatch prompt, incorporating prior attempt context."""
        if depth == 0:
            return task

        context_lines = []
        for prior in prior_results:
            context_lines.append(
                f"Attempt {prior.depth + 1}: {prior.refinement or 'initial'}"
            )
            # Include a summary, not the full result (context budget)
            summary = prior.result[:500] if len(prior.result) > 500 else prior.result
            context_lines.append(f"Result: {summary}")

        context = "\n".join(context_lines)
        return f"{task}\n\n## Prior attempts\n\n{context}\n\nFix what was missing."

    def refine_task(self, task: str, result: str, criterion: Criterion) -> str:
        """Generate a refinement note for the next dispatch."""
        return f"Criterion not met: {criterion.description}. Adjust approach."

    def dispatch(
        self,
        prompt: str,
        agent_type: str = "direct-implementation",
    ) -> str:
        """Dispatch to a worker agent and return the result.

        Override this method to wire in actual CC Agent tool calls.
        Default implementation returns the prompt (for testing).
        """
        return f"[Agent({agent_type}) would execute: {prompt[:200]}...]"

    def run(
        self,
        task: str,
        criterion: Criterion,
        agent_type: str = "direct-implementation",
    ) -> DispatchResult:
        """Execute the re-dispatch loop.

        Returns the final DispatchResult (criterion met or max_depth exhausted).
        """
        self.history.clear()

        for depth in range(self.max_depth):
            prompt = self.build_prompt(task, depth, self.history)
            result = self.dispatch(prompt, agent_type)

            met = criterion.evaluate(result)
            refinement = None if met else self.refine_task(task, result, criterion)

            dispatch_result = DispatchResult(
                depth=depth,
                result=result,
                criterion_met=met,
                refinement=refinement,
            )
            self.history.append(dispatch_result)

            if met:
                return dispatch_result

        # Max depth exhausted — return last result
        return self.history[-1]
