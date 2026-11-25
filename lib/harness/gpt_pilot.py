"""GPTPilotHarness — GPT-Pilot (Pythagora core) as a Harness leaf.

No console script exists: the package is `pythagora-core` (2.0.10) and
its pyproject.toml defines no [tool.poetry.scripts], so a `pythagora`
binary does not resolve. The scriptable entrypoint is
`python3 <checkout>/main.py`, which delegates argv handling to
core.cli.main.run_pythagora — the constructor therefore takes the
checkout directory and as_argv renders an absolute main.py path.

Closest scriptable start (used here): `--initial-prompt "<prompt>"` —
"Initial prompt to automatically start a new project with 'node'
stack" — skips the interactive what-do-you-want-to-build question and
proceeds straight to project creation; breakdown auto-confirm defaults
ON (only --no-auto-confirm-breakdown disables it, so the default aids
unattended runs).

Provenance (verified 2026-07-17, confidence HIGH — argparse quoted
from core/cli/helpers.py::parse_arguments; main.py and pyproject.toml
read at main):
- https://github.com/Pythagora-io/gpt-pilot
  (main.py, core/cli/main.py, core/cli/helpers.py, pyproject.toml)

Honest limits: the tool is INTERACTIVE-FIRST — --initial-prompt only
automates the first question; later steps still await user input over
stdin/IPC, so no fully headless one-shot mode exists and an unattended
dispatch ends at task.timeout_s as an honest failure. SECURITY: the
repo is unmaintained and carried a credential-stealing supply-chain
compromise in core/telemetry/ (Aug 2025 – 11 Jun 2026, per the repo's
own notice) — this adapter only renders argv (tests inject fake
runners); running a live checkout is an explicit caller choice.
available() only reflects python3 on PATH, not checkout presence.
"""
from __future__ import annotations

from pathlib import Path

from .base import Task, register
from .cli import CLIHarness


@register
class GPTPilotHarness(CLIHarness):
    """GPT-Pilot main.py start via --initial-prompt (checkout-based)."""

    KIND = "gpt-pilot"
    EXECUTABLE = "python3"

    def __init__(self, gpt_pilot_dir: Path | str, **kwargs) -> None:
        super().__init__(**kwargs)
        self._gpt_pilot_dir = Path(gpt_pilot_dir)

    def as_argv(self, task: Task) -> list[str]:
        return [self.EXECUTABLE,
                str(self._gpt_pilot_dir / "main.py"),
                "--initial-prompt", task.prompt]
