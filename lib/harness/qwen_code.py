"""QwenCodeHarness — Alibaba Qwen Code CLI (`qwen`) as a Harness leaf.

Qwen Code is Alibaba's open-source terminal coding agent, a gemini-cli
fork tuned for the Qwen coder models (github.com/QwenLM/qwen-code,
Apache-2.0). Non-interactive (headless) form:
`qwen -p <prompt> --approval-mode plan [-m <model>] [--sandbox]`.

Provenance (verified 2026-07-17, confidence high — official docs):
- qwenlm.github.io/qwen-code-docs/en/users/features/headless/:
  `-p/--prompt` runs headless; `-m/--model` selects the model (example
  value `qwen3-coder-plus`); `--approval-mode` takes `plan`, `default`,
  `auto-edit`, `auto`, `yolo`; `--yolo/-y` auto-approves everything
  (mutually exclusive with `--approval-mode`; this harness never emits
  it); `--sandbox` (no short form documented) opts into sandboxed tool
  execution; `--output-format text|json|stream-json` (default `text`,
  which this harness relies on).
- Same page: `--yolo` does NOT enable a sandbox — approval and
  sandboxing are independent axes.

Capability floor: default `--approval-mode plan` is the read-only
planning mode — edits and shell execution are not auto-approved, and a
headless run cannot prompt, so widening is impossible without an
explicit constructor choice (`approval_mode="auto-edit"` etc.).
`sandbox` defaults to False because qwen's sandbox requires a container
runtime / macOS seatbelt profile on the host and fails closed where
none is configured — enabling it is the caller's explicit,
host-verified choice, not an implicit default.
"""
from __future__ import annotations

from typing import Optional

from .base import Task, register
from .cli import CLIHarness


@register
class QwenCodeHarness(CLIHarness):
    """Qwen Code, headless, plan-mode (read-only) by default."""

    KIND = "qwen-code"
    EXECUTABLE = "qwen"

    def __init__(self, approval_mode: str = "plan",
                 model: Optional[str] = None, sandbox: bool = False,
                 **kwargs) -> None:
        super().__init__(**kwargs)
        self._approval_mode = approval_mode
        self._model = model
        self._sandbox = sandbox

    def as_argv(self, task: Task) -> list[str]:
        argv = [self.EXECUTABLE, "-p", task.prompt,
                "--approval-mode", self._approval_mode]
        if self._model:
            argv += ["-m", self._model]
        if self._sandbox:
            argv.append("--sandbox")
        return argv
