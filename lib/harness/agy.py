"""AgyHarness — Google Antigravity CLI (`agy`) as a Harness leaf.

Antigravity CLI is the continued gemini-cli lineage: Google retired the
open-source Gemini CLI in May 2026 and folded it into the closed-source
`agy` binary (devtoollab.com/blog/top-cli-ai-coding-agents; The New
Stack, "Gemini CLI vs. Antigravity"). Non-interactive form:
`agy -p <prompt> --sandbox --mode plan [--model <id>]`.

Provenance (verified 2026-07-17, confidence medium-high — flags
web-verified against agy v1.1.0 documentation, not run locally):
- toolsbase.dev/en/reference/antigravity-cli-commands (verified against
  agy v1.1.0): `-p/--print` runs one prompt non-interactively (alias
  `--prompt`); `--model gemini-3-pro` (no short form); `--sandbox` is a
  boolean that enables terminal restrictions; `--mode plan` (v1.1.0+)
  starts in plan mode instead of edit mode;
  `--dangerously-skip-permissions` auto-approves everything (this
  harness never emits it).
- github.com/google-antigravity/antigravity-cli/issues/45: in `-p` mode
  agy AUTO-APPROVES all tool calls including write_file, and
  `--sandbox` restricts terminal/shell tools only — not file writes.

Capability floor (honest limit): agy has no true read-only switch for
`-p` runs (issue #45 above). The default argv therefore layers BOTH
partial guards the tool does offer — `--sandbox` (terminal
restrictions) plus `--mode plan` (plan-not-edit, v1.1.0+) — but an
older client (pre-1.1.0, no `--mode`) or a regression can still write
files. This is filesystem best-effort, not a kernel guarantee (compare
GrokBuildHarness, whose sandbox is kernel-enforced). Dropping either
guard is an explicit constructor choice, never implicit.
"""
from __future__ import annotations

from typing import Optional

from .base import Task, register
from .cli import CLIHarness


@register
class AgyHarness(CLIHarness):
    """Antigravity CLI, non-interactive, double-guarded by default."""

    KIND = "agy"
    EXECUTABLE = "agy"

    def __init__(self, model: Optional[str] = None, sandbox: bool = True,
                 mode: Optional[str] = "plan", **kwargs) -> None:
        super().__init__(**kwargs)
        self._model = model
        self._sandbox = sandbox
        self._mode = mode

    def as_argv(self, task: Task) -> list[str]:
        argv = [self.EXECUTABLE, "-p", task.prompt]
        if self._sandbox:
            argv.append("--sandbox")
        if self._mode:
            argv += ["--mode", self._mode]
        if self._model:
            argv += ["--model", self._model]
        return argv
