"""SWEAgentHarness — SWE-agent's ``sweagent run`` as a Harness leaf.

Non-interactive form (SWE-agent >= 1.x hierarchical CLI; verified
against swe-agent.com/latest/usage/cl_tutorial/ and /usage/cli/,
2026-07-17):

    sweagent run --agent.model.name=<model>
        --agent.model.per_instance_cost_limit=<usd>
        [--env.repo.path=<dir>] --problem_statement.text=<prompt>

Flags are hierarchical dotted keys in ``=`` form; the prompt travels
as ``--problem_statement.text`` (the flat ``--problem_statement`` /
``--task`` shapes of the pre-1.0 CLI no longer exist — the CLI changed
across versions, hence the pin to the documented current shape).

Capability floor by construction: SWE-agent executes inside its Docker
deployment environment by default (the docs' ``--env.deployment.image``
knob overrides an image that is otherwise defaulted), and this adapter
always pins ``--agent.model.per_instance_cost_limit`` (default 2.00 —
the docs' own example cap) so a runaway run is spend-bounded. Repo
targeting is an explicit constructor choice (``repo_path``); no repo
flag is emitted unless the caller names one.

Confidence: high (official current documentation).
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from .base import Task, register
from .cli import CLIHarness


@register
class SWEAgentHarness(CLIHarness):
    """SWE-agent single-issue run — cost-capped, docker-deployed."""

    KIND = "swe-agent"
    EXECUTABLE = "sweagent"

    def __init__(self, model: str = "gpt-4o", cost_limit: float = 2.0,
                 repo_path: Optional[Path] = None, **kwargs) -> None:
        super().__init__(**kwargs)
        self._model = model
        self._cost_limit = cost_limit
        self._repo_path = Path(repo_path) if repo_path else None

    def as_argv(self, task: Task) -> list[str]:
        argv = [self.EXECUTABLE, "run",
                f"--agent.model.name={self._model}",
                "--agent.model.per_instance_cost_limit="
                f"{self._cost_limit:.2f}"]
        if self._repo_path is not None:
            argv.append(f"--env.repo.path={self._repo_path}")
        argv.append(f"--problem_statement.text={task.prompt}")
        return argv
