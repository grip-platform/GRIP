"""CodeWhaleHarness — CodeWhale (ex DeepSeek-TUI) headless exec leaf.

CodeWhale is the v0.8.41+ rename of DeepSeek-TUI (docs/REBRAND.md in
Hmbown/CodeWhale, verified 2026-07-17): the command is ``codewhale``
(npm package ``codewhale``); the legacy ``deepseek`` / ``deepseek-tui``
shim binaries were REMOVED in v0.9.0, so this adapter targets the
renamed binary only.

Non-interactive form: ``codewhale exec "<task>"`` — the README's own
headless "for scripts and CI" shape, the analogue of Claude Code's
``-p`` (github.com/Hmbown/CodeWhale README + the deepseek-tui.app
rename/install guide, verified 2026-07-17).

Honest limit: the README documents NO flags on ``exec``. Model
selection (the ``/model`` slash command) and the Ask / Auto-Review /
Full-Access approval posture are TUI/config controls, not argv, so
this adapter emits exactly the documented two-token invocation and a
run inherits the user's ``~/.codewhale`` configuration — keep that
config's approval posture conservative; sandboxing (Seatbelt /
Landlock+seccomp / bwrap) is a runtime feature, not a flag. No
fabricated ``--model`` / ``--sandbox`` options.

Confidence: high on binary + exec shape and on the rename lineage
(official README + official REBRAND.md).
"""
from __future__ import annotations

from .base import Task, register
from .cli import CLIHarness


@register
class CodeWhaleHarness(CLIHarness):
    """CodeWhale headless exec — documented argv only, config-driven."""

    KIND = "codewhale"
    EXECUTABLE = "codewhale"

    def as_argv(self, task: Task) -> list[str]:
        return [self.EXECUTABLE, "exec", task.prompt]
