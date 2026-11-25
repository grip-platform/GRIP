"""
GRIP Channel — Message bridge for WhatsApp/Slack/Discord to Claude Code.

CC 2.1.80 introduced Channels: MCP servers that push events into running
sessions. This package provides the core abstraction layer:

- bridge.py: MessageBridge protocol + InboundMessage/OutboundMessage types
- router.py: Routes inbound messages to GRIP skills/commands
- adapters/: Platform-specific adapters (WhatsApp, Slack, Discord)
- security.py: Sender allowlist and rate limiting

Architecture:
    Platform -> Adapter -> MessageBridge -> Router -> GRIP Command -> Response
                                                                        |
    Platform <- Adapter <- MessageBridge <- Router <--------------------+
"""

from channel.bridge import MessageBridge, InboundMessage, OutboundMessage
from channel.router import MessageRouter
from channel.security import SenderAllowlist

__all__ = [
    "MessageBridge",
    "InboundMessage",
    "OutboundMessage",
    "MessageRouter",
    "SenderAllowlist",
]
