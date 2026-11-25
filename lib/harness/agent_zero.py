"""AgentZeroHarness — frdel/agent-zero terminal CLI as a Harness leaf.

Agent Zero is a checkout-run framework, not a console script: the
terminal entrypoint is ``run_cli.py`` at the checkout root, launched
as ``python run_cli.py`` with NO command-line arguments — no argparse,
no sys.argv use; it drives an interactive ``input("> ")`` chat loop
where the documented ``e`` command exits (source of run_cli.py read
via the dasarpai/AgenticAI-agent-zero mirror of the original
frdel/agent-zero layout, verified 2026-07-17; launcher split
documented at github.com/frdel/agent-zero docs, v0.6+). Current
agent0ai/agent-zero main has REMOVED run_cli.py (Docker/web-UI only:
run_ui.py, run_tunnel.py — repo listing verified 2026-07-17), so this
adapter targets a checkout that still ships it (frdel-era <= v0.8.x).

Honest limit: Agent Zero has NO native one-shot flag. The closest
scriptable entrypoint is the real interactive CLI driven over stdin:
as_argv renders one POSIX-shell pipeline that cds into the checkout
(run_cli.py imports ``initialize`` relative to its own directory),
pipes the prompt plus the documented ``e`` exit command into
run_cli.py, and lets the loop terminate normally after one turn.
That is stdin scripting of a mode that exists — not a fabricated
flag. POSIX /bin/sh only; a mid-stream ``input()`` after stdin EOF
raises EOFError and surfaces as an honest failed result.

Confidence: high that run_cli.py takes no argv (source read); medium
that the one-turn pipeline holds across every historical tag (loop
shape verified on the mirrored source only).
"""
from __future__ import annotations

import shlex
from pathlib import Path

from .base import Task, register
from .cli import CLIHarness


@register
class AgentZeroHarness(CLIHarness):
    """frdel/agent-zero run_cli.py, stdin-scripted for one turn."""

    KIND = "agent-zero"
    EXECUTABLE = "python3"

    def __init__(self, agent_zero_dir: Path | str,
                 python: str = "python3", **kwargs) -> None:
        super().__init__(**kwargs)
        self._dir = Path(agent_zero_dir)
        self._python = python

    def as_argv(self, task: Task) -> list[str]:
        """One /bin/sh pipeline: cd checkout, pipe prompt + 'e' exit
        into run_cli.py. Prompt is shlex-quoted — hostile text stays
        one printf argument and cannot break the pipeline."""
        pipeline = (
            f"cd {shlex.quote(str(self._dir))} && "
            f"printf '%s\\n' {shlex.quote(task.prompt)} e | "
            f"{shlex.quote(self._python)} run_cli.py"
        )
        return ["/bin/sh", "-c", pipeline]
