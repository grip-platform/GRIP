"""Speaker — the canonical AI syscall.

Every Speaker (LLM, Agent, Team, Agora) accepts a Message and returns a Response.
Substitutability is enforced by the LSP test suite (tests/test_speaker_lsp.py).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Optional


@dataclass(frozen=True)
class Message:
    """Polymorphic dispatch envelope — same shape for any Speaker.

    Frozen so the same Message can be sent to multiple Speakers without aliasing.
    """
    id: str
    payload: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("Message.id must be non-empty")
        if not isinstance(self.payload, str):
            raise TypeError("Message.payload must be a string")


@dataclass(frozen=True)
class Response:
    """Polymorphic response — what every Speaker returns.

    Invariants (verified by LSP tests):
      - .id echoes Message.id
      - .content is non-empty
      - .kind is one of the four canonical kinds
      - sub_responses is None for leaves, list[Response] for composites
    """
    id: str
    content: str
    kind: str               # "llm" | "agent" | "team" | "agora"
    speaker_id: str
    sub_responses: Optional[list["Response"]] = None
    metadata: dict[str, Any] = field(default_factory=dict)


# A Synthesiser turns a list of sub-responses into a single string.
# Composites take one at construction (DI). Allows different synthesis strategies
# (concat / debate / vote / weighted) without changing the Speaker interface.
Synthesiser = Callable[[list[Response]], str]


def default_synthesiser(responses: list[Response]) -> str:
    """Concat synthesiser — joins all sub-responses with separators."""
    return "\n\n---\n\n".join(r.content for r in responses)


class Speaker(ABC):
    """The Liskov-substitutable contract.

    Implementors MUST honour these pre/post conditions:

    Pre-conditions on send_message(msg):
      - msg is a valid Message (any well-formed Message)
      - implementor MUST NOT strengthen pre-conditions

    Post-conditions on send_message returning Response:
      - Response.id == msg.id (echoed identifier)
      - Response.content is non-empty
      - Response.kind == self.kind
      - implementor MUST NOT weaken post-conditions

    Invariants:
      - Same msg.id within session window returns Response with same .id
      - .kind is stable for the lifetime of the Speaker
      - No isinstance() branches in non-leaf methods (composite dispatch is by .members)
    """

    @abstractmethod
    def send_message(self, msg: Message) -> Response:
        """Single canonical interaction. Sync from caller's perspective."""

    @property
    @abstractmethod
    def kind(self) -> str:
        """One of: 'llm', 'agent', 'team', 'agora'."""

    @property
    @abstractmethod
    def speaker_id(self) -> str:
        """Stable identifier for this Speaker instance."""
