"""
MessageBridge — Core protocol for GRIP Channel communication.

Defines the message types and bridge interface that all platform adapters
must implement. The bridge is stateless: state lives in GRIP, not here.

CC Channels push events via MCP server protocol. This bridge sits between
the MCP layer and GRIP's command/skill execution.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Protocol, runtime_checkable


class Platform(Enum):
    """Supported messaging platforms."""
    WHATSAPP = "whatsapp"
    SLACK = "slack"
    DISCORD = "discord"
    TELEGRAM = "telegram"


class MessageType(Enum):
    """Types of inbound messages."""
    TEXT = "text"
    COMMAND = "command"       # Starts with / (GRIP command)
    PERMISSION = "permission" # Approve/deny tool use from phone
    MEDIA = "media"           # Image, file, voice note
    REACTION = "reaction"     # Emoji reaction to a message


@dataclass(frozen=True)
class InboundMessage:
    """A message received from a platform adapter.

    Immutable: once received, the message cannot be modified.
    The router inspects this to determine what GRIP action to take.
    """
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    platform: Platform = Platform.WHATSAPP
    sender_id: str = ""
    sender_name: str = ""
    content: str = ""
    message_type: MessageType = MessageType.TEXT
    timestamp: float = field(default_factory=time.time)
    reply_to: Optional[str] = None  # ID of message being replied to
    metadata: dict = field(default_factory=dict)

    @property
    def is_command(self) -> bool:
        return self.message_type == MessageType.COMMAND or self.content.startswith("/")

    @property
    def is_permission_response(self) -> bool:
        return self.message_type == MessageType.PERMISSION


@dataclass
class OutboundMessage:
    """A message to send back to a platform.

    Mutable: the router may enrich the message before sending.
    """
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    platform: Platform = Platform.WHATSAPP
    recipient_id: str = ""
    content: str = ""
    reply_to: Optional[str] = None  # ID of inbound message this replies to
    metadata: dict = field(default_factory=dict)


@runtime_checkable
class MessageBridge(Protocol):
    """Protocol that all platform adapters must implement.

    The bridge is the contract between GRIP Channel and each platform.
    Adapters handle platform-specific API calls; the bridge defines
    what data flows in and out.

    Lifecycle:
        1. connect() — establish connection to platform API
        2. receive() — called by MCP server when a message arrives
        3. send() — called by router when GRIP has a response
        4. disconnect() — clean shutdown
    """

    @property
    def platform(self) -> Platform:
        """Which platform this bridge connects to."""
        ...

    @property
    def is_connected(self) -> bool:
        """Whether the bridge has an active connection."""
        ...

    async def connect(self) -> None:
        """Establish connection to the platform API."""
        ...

    async def disconnect(self) -> None:
        """Clean shutdown of the platform connection."""
        ...

    async def send(self, message: OutboundMessage) -> bool:
        """Send a message to the platform. Returns True on success."""
        ...

    async def on_message(self, raw: dict) -> Optional[InboundMessage]:
        """Parse a raw platform event into an InboundMessage.

        Returns None if the event should be ignored (e.g. bot's own messages,
        unsupported event types).
        """
        ...
