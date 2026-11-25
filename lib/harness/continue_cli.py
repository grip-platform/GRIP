"""ContinueCLIHarness — Continue CLI (`cn`) headless mode as a leaf.

Non-interactive form: `cn [flags] -p "prompt"` — the docs' headless
mode ("run prompt and exit; the agent runs to completion and prints
its response to stdout", intended for scripts/CI/git hooks). The
``-p``/prompt pair is kept adjacent and last so the argv parses the
same whether `-p` takes the prompt as its value or as a following
positional.

Capability floor (untrusted-content-capability-lock discipline):
default ``readonly=True`` appends ``--readonly`` — the docs' "Plan
mode — read-only tools only" — so a headless run can neither write nor
stall on a write-tool approval prompt. Two explicit widenings:
``readonly=False`` drops the plan-mode lock (tool approvals may then
stall a headless run under the tool's defaults), and ``auto=True``
appends ``--auto`` ("Allow all tools without prompting") — the full
write-capable headless shape; callers choosing it own that blast
radius. Model/config are appended only when set, so the operator's own
Continue configuration stays authoritative.

Provenance: https://docs.continue.dev/cli/quickstart (headless `-p`,
`--model`, `--config`, `--readonly`, `--auto`, `--allow/--exclude`),
verified 2026-07-17. Confidence: high on flags; the binary is never
spawned here — tests inject fake runners.
"""
from __future__ import annotations

from typing import Optional

from .base import Task, register
from .cli import CLIHarness


@register
class ContinueCLIHarness(CLIHarness):
    """Continue CLI (`cn -p`), read-only plan mode by default."""

    KIND = "continue-cli"
    EXECUTABLE = "cn"

    def __init__(self, model: Optional[str] = None,
                 config: Optional[str] = None, readonly: bool = True,
                 auto: bool = False, **kwargs) -> None:
        super().__init__(**kwargs)
        self._model = model
        self._config = config
        self._readonly = readonly
        self._auto = auto

    def as_argv(self, task: Task) -> list[str]:
        argv = [self.EXECUTABLE]
        if self._readonly:
            argv.append("--readonly")
        if self._auto:
            argv.append("--auto")
        if self._model:
            argv += ["--model", self._model]
        if self._config:
            argv += ["--config", self._config]
        return argv + ["-p", task.prompt]
