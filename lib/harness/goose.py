"""GooseHarness — Block's goose CLI as a Harness leaf.

Non-interactive form: `goose run -t <prompt> --no-session` (verified
2026-07-17 against the goose headless-mode guide
https://goose-docs.ai/docs/tutorials/headless-goose/ and
https://github.com/block/goose — confidence HIGH for `run -t` and
`--no-session`; MEDIUM for `--max-turns`, which surfaces in goose
discussion #7495 but not on the headless guide, so it is absent from
the default argv and opt-in via the constructor).

Capability floor (rules/untrusted-content-capability-lock.md): goose
has NO read-only headless mode — auto mode approves tool use, and the
approve/smart-approve modes refuse non-interactive runs outright. The
floor here is therefore containment, not permission: `--no-session`
(no session-history residue) by default, plus an optional bounded
`--max-turns` against runaway execution. Widening (persistent
sessions, unbounded turns) is an explicit caller choice.

The default runner sets `GOOSE_MODE=auto` ONLY when the environment
does not already define it (headless goose fails by design without
auto mode); a caller-set value is never overridden. Tests inject fake
runners and never touch the environment or spawn goose.
"""
from __future__ import annotations

import os
import subprocess
from typing import Optional, Sequence

from .base import Task, register
from .cli import CLIHarness


def _goose_env_runner(argv: Sequence[str],
                      timeout_s: float) -> tuple[int, str, str]:
    """Subprocess runner supplying GOOSE_MODE=auto when unset — the
    documented requirement for headless goose (approve modes refuse
    non-interactive runs). Never overrides a caller-set value."""
    env = dict(os.environ)
    env.setdefault("GOOSE_MODE", "auto")
    proc = subprocess.run(list(argv), capture_output=True, text=True,
                          timeout=timeout_s, env=env)
    return proc.returncode, proc.stdout, proc.stderr


@register
class GooseHarness(CLIHarness):
    """Block goose, non-interactive one-shot (`goose run -t`)."""

    KIND = "goose"
    EXECUTABLE = "goose"

    def __init__(self, no_session: bool = True,
                 max_turns: Optional[int] = None, **kwargs) -> None:
        if kwargs.get("runner") is None:
            kwargs["runner"] = _goose_env_runner
        super().__init__(**kwargs)
        self._no_session = no_session
        self._max_turns = max_turns

    def as_argv(self, task: Task) -> list[str]:
        argv = [self.EXECUTABLE, "run", "-t", task.prompt]
        if self._no_session:
            argv.append("--no-session")
        if self._max_turns is not None:
            argv += ["--max-turns", str(self._max_turns)]
        return argv
