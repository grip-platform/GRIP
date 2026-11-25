"""OmnigentHarness — the Omnigent META-harness as a Harness leaf.

Omnigent (Apache-2.0, open-sourced by Databricks 2026-06) is itself a
meta-harness: one orchestration layer wrapping Claude Code, Codex,
Cursor, OpenCode, Pi, and custom YAML agents, with OS sandboxing
enforced automatically at that layer (bubblewrap on Linux, seatbelt on
macOS) rather than via per-run flags. Registering it here nests one
meta-harness inside another: GRIP's composition tree treats Omnigent
as a single leaf whose internals fan out to its own guest harnesses.

Non-interactive form:

    omnigent run [agent] [--harness H] [--model M] -p <prompt>

Sources: official README for the binary names (``omnigent`` / ``omni``
alias), the ``run`` subcommand, ``--harness``, ``--model``, and the
automatic sandbox model (github.com/omnigent-ai/omnigent, verified
2026-07-17); the one-shot ``-p`` prompt flag comes from the Developers
Digest review example ``omnigent run --harness codex -p "explain this
repo"`` (developersdigest.tech/blog/omnigent-meta-harness-agent-
orchestration, verified 2026-07-17). ``-p`` is NOT shown in the
official README, so confidence on that flag is medium; the rest is
high.

Capability discipline: defaults emit NO harness/model/agent selection
— Omnigent's own credential pick and always-on sandbox floor apply.
Choosing a guest harness, model, or agent definition is an explicit
constructor decision, never implicit.
"""
from __future__ import annotations

from typing import Optional

from .base import Task, register
from .cli import CLIHarness


@register
class OmnigentHarness(CLIHarness):
    """Omnigent one-shot run — a meta-harness nested as a GRIP leaf."""

    KIND = "omnigent"
    EXECUTABLE = "omnigent"

    def __init__(self, harness: Optional[str] = None,
                 model: Optional[str] = None,
                 agent: Optional[str] = None, **kwargs) -> None:
        super().__init__(**kwargs)
        self._harness = harness
        self._model = model
        self._agent = agent

    def as_argv(self, task: Task) -> list[str]:
        argv = [self.EXECUTABLE, "run"]
        if self._agent:
            argv.append(self._agent)
        if self._harness:
            argv += ["--harness", self._harness]
        if self._model:
            argv += ["--model", self._model]
        argv += ["-p", task.prompt]
        return argv
