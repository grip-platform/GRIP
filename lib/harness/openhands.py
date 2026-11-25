"""OpenHandsHarness — OpenHands (ex-OpenDevin) CLI as a Harness leaf.

Non-interactive form: `openhands --headless -t <prompt>` (verified
2026-07-17 against
https://docs.openhands.dev/openhands/usage/cli/headless, the command
reference at
https://docs.openhands.dev/openhands/usage/cli/command-reference, and
https://github.com/OpenHands/OpenHands-CLI — confidence HIGH).
Optional `--json` streams JSONL events instead of plain text.

Honest limits (stated, never papered over):
- Headless mode HARDCODES always-approve: every action executes
  without confirmation, `--llm-approve` is rejected in headless, and
  no flag narrows the surface. There is no read-only mode to pin.
- No workspace-confinement flag exists on the CLI (the command
  reference's only mount knob, `serve --mount-cwd`, belongs to the
  container mode). The workspace is the process cwd, so confinement
  is the caller's choice of working directory for the spawned
  process — reserve this harness for controlled environments.

Install note: the current CLI ships as `uv tool install openhands`
(PyPI `openhands`, Python 3.12+); `pip install openhands-ai` is the
legacy V0 SDK, not this binary.
"""
from __future__ import annotations

from .base import Task, register
from .cli import CLIHarness


@register
class OpenHandsHarness(CLIHarness):
    """OpenHands headless one-shot (`openhands --headless -t`)."""

    KIND = "openhands"
    EXECUTABLE = "openhands"

    def __init__(self, json_output: bool = False, **kwargs) -> None:
        super().__init__(**kwargs)
        self._json_output = json_output

    def as_argv(self, task: Task) -> list[str]:
        argv = [self.EXECUTABLE, "--headless", "-t", task.prompt]
        if self._json_output:
            argv.append("--json")
        return argv
