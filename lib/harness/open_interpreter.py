"""OpenInterpreterHarness — Open Interpreter (`interpreter`) as a leaf.

Two lineages, one honest target. The LEGACY Python CLI (PyPI
open-interpreter 0.4.3, docs.openinterpreter.com) is an interactive
REPL whose only headless affordance is `-y`/auto_run — blanket
auto-approval of LOCAL code execution with no sandbox and no documented
one-shot prompt flag. That shape is structurally unfit for a safe
headless harness, so it is NOT adapted here. The CURRENT Open
Interpreter (github.com/openinterpreter/openinterpreter) is a Codex
fork that "speaks the same Codex exec protocol"; its CLI reference
documents `interpreter exec "prompt"` with kernel sandbox modes.

Non-interactive form used here:
`interpreter exec --sandbox <mode> [--model M] "prompt"`.

Capability trade-off (GrokBuildHarness discipline): Open Interpreter's
purpose is running code on the host, so headless dispatch is gated by
the SANDBOX, not by trust. Default ``sandbox="read-only"`` mirrors
CodexHarness — the flag's documented values are read-only /
workspace-write / danger-full-access, and any widening is an explicit
constructor choice. Honest limit: the sandbox is the tool's own
enforcement, this shim only pins the flag; approval policy
(`--ask-for-approval`) is left at the tool default, so a widened
sandbox under a prompting policy may stall headless — callers widening
the sandbox own that trade-off.

Provenance: repo README (raw, "Supports `exec` ... speaks the same
Codex exec protocol") + docs/cli-reference.md (`interpreter exec
"fix the failing test"`; `--sandbox, -s` read-only|workspace-write|
danger-full-access; `--model, -m`), verified 2026-07-17. Confidence:
high on argv shape (quoted from the CLI reference); the binary is
never spawned here — tests inject fake runners.
"""
from __future__ import annotations

from typing import Optional

from .base import Task, register
from .cli import CLIHarness


@register
class OpenInterpreterHarness(CLIHarness):
    """Open Interpreter `exec`, read-only sandbox by default."""

    KIND = "open-interpreter"
    EXECUTABLE = "interpreter"

    def __init__(self, sandbox: str = "read-only",
                 model: Optional[str] = None, **kwargs) -> None:
        super().__init__(**kwargs)
        self._sandbox = sandbox
        self._model = model

    def as_argv(self, task: Task) -> list[str]:
        argv = [self.EXECUTABLE, "exec", "--sandbox", self._sandbox]
        if self._model:
            argv += ["--model", self._model]
        return argv + [task.prompt]
