"""PiHarness — the pi coding agent CLI as a Harness leaf.

pi is the minimal terminal coding harness from the pi-mono toolkit
(originally github.com/badlogic/pi-mono; the repository is canonically
earendil-works/pi as of 2026-07 — GitHub serves the old path under the
new owner). Disambiguation: this is the coding agent listed in
github.com/bradagi/awesome-cli-coding-agents — NOT Inflection AI's
"Pi" chatbot and NOT the separate oh-my-pi fork. Non-interactive
(print) form: `pi -p <prompt> --tools read,grep,find,ls`.

Provenance (verified 2026-07-17, confidence high — official docs):
- github.com/badlogic/pi-mono/blob/main/packages/coding-agent/docs/
  usage.md: `-p/--print` prints the response and exits (piped stdin is
  merged into the prompt); `--tools <csv>` allowlists built-in tools
  from {read, bash, edit, write, grep, find, ls}; the documented
  read-only recipe is `pi --tools read,grep,find,ls -p "..."`;
  `--model <pattern>` selects a model (supports `provider/id`, no
  short form); `--provider <name>` exists but `provider/id` via
  `--model` subsumes it for this harness's single knob.
- Same page: non-interactive modes show no trust prompt (they use the
  saved/default project trust) — a runtime concern, not an argv one.

Capability floor: default tool allowlist is the read-only quartet
(read, grep, find, ls) from the official docs — bash/edit/write are
absent by construction. Widening (e.g. tools=("read", "bash")) is an
explicit constructor choice, never implicit
(rules/untrusted-content-capability-lock.md).
"""
from __future__ import annotations

from typing import Optional, Sequence

from .base import Task, register
from .cli import CLIHarness

DEFAULT_PI_TOOLS = ("read", "grep", "find", "ls")


@register
class PiHarness(CLIHarness):
    """pi coding agent, print mode, read-only tool allowlist default."""

    KIND = "pi"
    EXECUTABLE = "pi"

    def __init__(self, tools: Sequence[str] = DEFAULT_PI_TOOLS,
                 model: Optional[str] = None, **kwargs) -> None:
        super().__init__(**kwargs)
        self._tools = tuple(tools)
        self._model = model

    def as_argv(self, task: Task) -> list[str]:
        argv = [self.EXECUTABLE, "-p", task.prompt,
                "--tools", ",".join(self._tools)]
        if self._model:
            argv += ["--model", self._model]
        return argv
