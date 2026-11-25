"""HALHarness — GRIP's Pool-B provider ladder adapted to the Harness contract.

A degenerate harness (bare LLM call, no tool loop) — documented honestly,
fully substitutable. The infer callable is injected (DIP); the default
lazy-imports lib/hal_llm_adapter.LlamaEngine.chat, which never raises and
returns an empty string on total provider failure — converted here into
an honest failed result.

This module is also the HAL-in-HAL seam: the engine underneath may itself
route to the VPS sovereign-council endpoint (local :4010 gateway ->
Tailscale council endpoint, ADR 0012) — a harness hosting a harness at
the provider layer, already live in production.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Callable, Optional

from .base import Harness, HarnessResult, Task, register


def _default_infer(prompt: str) -> str:
    lib_dir = Path(__file__).resolve().parent.parent
    if str(lib_dir) not in sys.path:
        sys.path.insert(0, str(lib_dir))
    from hal_llm_adapter import LlamaEngine
    return LlamaEngine().chat([{"role": "user", "content": prompt}])


@register
class HALHarness(Harness):
    """Bare provider call through HAL's failover ladder (Pool B, $0 weekly)."""

    KIND = "hal"

    def __init__(self, infer: Optional[Callable[[str], str]] = None,
                 **kwargs) -> None:
        super().__init__(**kwargs)
        self._infer = infer or _default_infer

    def _execute(self, task: Task) -> HarnessResult:
        content = (self._infer(task.prompt) or "").strip()
        if content:
            return HarnessResult(id=task.id, kind=self.kind,
                                 harness_id=self.harness_id, ok=True,
                                 content=content)
        return self.fail(task, "provider returned empty content")
