"""KiloHarness — Kilo (Kilo Code) CLI as a Harness leaf.

Non-interactive form: `kilo run --auto <prompt>` (verified 2026-07-17
against https://kilo.ai/docs/cli and https://kilo.ai/cli — npm package
`@kilocode/cli`, binary `kilo`, built on OpenCode; confidence HIGH).
Without `--auto` the CLI opens its interactive TUI, so `--auto` IS
the non-interactive shape: permission prompts are disabled and
follow-up questions are auto-answered with an instruction to decide
autonomously.

Capability discipline (honest limits): kilo exposes NO per-run model,
mode, or permission flags — model/provider come from config or env
(`KILO_PROVIDER`, `KILOCODE_MODEL`; `KILO_ORG_ID` for CI), and
permissions come from `~/.config/kilo/kilo.jsonc` (per-tool
allow/ask/deny patterns). The one fail-closed property documented for
autonomous mode: operations not auto-approved by that config are
refused, never prompted. There is nothing narrower to pin from argv,
so this adapter adds no fake knobs; scoping a run is the caller's
permission-config job. Kilo exits 124 on its own timeout; the
injected runner's subprocess timeout still governs the dispatch.
"""
from __future__ import annotations

from .base import Task, register
from .cli import CLIHarness


@register
class KiloHarness(CLIHarness):
    """Kilo Code CLI, autonomous non-interactive run."""

    KIND = "kilo"
    EXECUTABLE = "kilo"

    def as_argv(self, task: Task) -> list[str]:
        return [self.EXECUTABLE, "run", "--auto", task.prompt]
