"""ShellGPTHarness — TheR1D/shell_gpt (`sgpt`) as a Harness leaf.

Non-interactive form: `sgpt [--model M] [--code] "prompt"` — the prompt
is a positional argument and a bare `sgpt "prompt"` is a pure Q&A call:
"No command execution occurs without --shell" (project README).

Capability floor (mirrors CodexHarness / GrokBuildHarness discipline):
the default argv generates TEXT only. Two explicit widenings exist —

- ``code=True`` appends ``--code`` ("Generate only code"): still pure
  text generation, narrower output register.
- ``shell=True`` appends ``--shell --no-interaction``. Honest limit:
  sgpt's shell EXECUTION lives behind the interactive
  [E]xecute/[D]escribe/[A]bort prompt, which would hang a headless
  subprocess; ``--no-interaction`` disables that prompt and writes the
  generated command to stdout instead. So even the widened shape only
  ever GENERATES a shell command (returned as result content) — this
  harness never lets sgpt execute one.

Model is only appended when the caller sets it, so the tool's own
default (README currently documents gpt-5.4-mini) stays authoritative.

Provenance: https://github.com/TheR1D/shell_gpt README (flags table +
positional-prompt examples), verified 2026-07-17. Confidence: high on
argv shape; runtime behaviour is never spawned here — tests inject fake
runners.
"""
from __future__ import annotations

from typing import Optional

from .base import Task, register
from .cli import CLIHarness


@register
class ShellGPTHarness(CLIHarness):
    """shell_gpt (`sgpt`), non-interactive single-shot Q&A by default."""

    KIND = "shell-gpt"
    EXECUTABLE = "sgpt"

    def __init__(self, model: Optional[str] = None, code: bool = False,
                 shell: bool = False, **kwargs) -> None:
        super().__init__(**kwargs)
        self._model = model
        self._code = code
        self._shell = shell

    def as_argv(self, task: Task) -> list[str]:
        argv = [self.EXECUTABLE]
        if self._model:
            argv += ["--model", self._model]
        if self._code:
            argv.append("--code")
        if self._shell:
            argv += ["--shell", "--no-interaction"]
        return argv + [task.prompt]
