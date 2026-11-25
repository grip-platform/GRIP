"""FabricHarness — danielmiessler/fabric as a Harness leaf.

Fabric is a pattern-based one-shot text pipeline: input goes to a named
prompt pattern, output is text. Non-interactive form used here:
`fabric --pattern <name> [--model M] "prompt"` — the README shows the
prompt both piped via stdin and passed as a trailing positional
argument (`fabric --pattern my-analyzer "analyze this text"`). This
harness uses the positional form because the injected RunnerFn contract
is argv-only (no stdin channel), which also keeps the argv relayable by
composite harnesses.

Capability floor by construction: fabric transforms text; it executes
nothing. The default pattern is ``summarize`` — a canonical, read-only
pattern that ships with fabric — so the default argv is a verified
invocation shape rather than an undocumented pattern-less call.
Choosing another pattern or model is an explicit constructor choice.

Provenance: https://github.com/danielmiessler/fabric README (flags
`-p/--pattern`, `-m/--model`; positional-prompt example), verified
2026-07-17. Confidence: high on flags, medium-high on the positional
prompt (one README example; stdin is the dominant documented style).
"""
from __future__ import annotations

from typing import Optional

from .base import Task, register
from .cli import CLIHarness


@register
class FabricHarness(CLIHarness):
    """fabric pattern runner, non-interactive one-shot."""

    KIND = "fabric"
    EXECUTABLE = "fabric"

    def __init__(self, pattern: str = "summarize",
                 model: Optional[str] = None, **kwargs) -> None:
        super().__init__(**kwargs)
        self._pattern = pattern
        self._model = model

    def as_argv(self, task: Task) -> list[str]:
        argv = [self.EXECUTABLE, "--pattern", self._pattern]
        if self._model:
            argv += ["--model", self._model]
        return argv + [task.prompt]
