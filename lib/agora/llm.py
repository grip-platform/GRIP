"""LLMSpeaker — leaf Speaker that calls a language model directly.

Wraps a callable (real HAL router or a test stub) so the same Speaker
interface is honoured. No isinstance, no composition.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from lib.agora.speaker import Message, Response, Speaker

# A LLMCallable takes a prompt string, returns a content string.
# Real impl: lib.hal.router.Router().route(prompt). Test impl: lambda p: f"echo:{p}".
LLMCallable = Callable[[str], str]


@dataclass
class LLMSpeaker(Speaker):
    """Leaf Speaker. Calls the underlying LLM via the injected callable."""

    provider: str
    model: str
    call: LLMCallable

    @property
    def kind(self) -> str:
        return "llm"

    @property
    def speaker_id(self) -> str:
        return f"{self.provider}/{self.model}"

    def send_message(self, msg: Message) -> Response:
        content = self.call(msg.payload)
        if not content:
            content = "[empty]"  # post-condition guard
        return Response(
            id=msg.id,
            content=content,
            kind=self.kind,
            speaker_id=self.speaker_id,
            sub_responses=None,
            metadata={"provider": self.provider, "model": self.model},
        )
