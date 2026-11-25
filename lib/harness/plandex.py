"""PlandexHarness — Plandex CLI (`plandex tell`) as a Harness leaf.

Non-interactive form: `plandex tell <prompt> --skip-menu --no-exec`
(verified 2026-07-17 against the CLI reference,
https://github.com/plandex-ai/plandex/blob/main/docs/docs/cli-reference.md,
and https://github.com/plandex-ai/plandex — confidence HIGH; every
flag below appears verbatim in that reference). `--skip-menu`
suppresses the post-response interactive menu — the scriptable shape.

Capability floor (rules/untrusted-content-capability-lock.md):
plandex accumulates changes in its own version-controlled sandbox and
only touches project files on apply. The floor pins the safe shape
explicitly: `--no-exec` forbids command execution, and `--apply` is
NOT passed, so nothing lands in the working tree. Honest residual:
`--apply` "defaults to config value auto-apply", so a plan the caller
configured for full autonomy could still auto-apply without the flag
— the stock config keeps auto-apply off, and this adapter never
enables it. Widening is a constructor choice: apply=True adds
`--apply`; auto_exec=True swaps `--no-exec` for `--auto-exec` (which
plandex's own docs warn can run destructive commands).

Deployment note: Plandex Cloud stopped accepting new users on
2025-10-03 — current use is self-hosted/local mode with provider keys
(per the project README), which does not change the argv shape.
"""
from __future__ import annotations

from .base import Task, register
from .cli import CLIHarness


@register
class PlandexHarness(CLIHarness):
    """Plandex scriptable tell — sandbox-only, no exec, no apply."""

    KIND = "plandex"
    EXECUTABLE = "plandex"

    def __init__(self, apply: bool = False, auto_exec: bool = False,
                 **kwargs) -> None:
        super().__init__(**kwargs)
        self._apply = apply
        self._auto_exec = auto_exec

    def as_argv(self, task: Task) -> list[str]:
        argv = [self.EXECUTABLE, "tell", task.prompt, "--skip-menu"]
        argv.append("--auto-exec" if self._auto_exec else "--no-exec")
        if self._apply:
            argv.append("--apply")
        return argv
