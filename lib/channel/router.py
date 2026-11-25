"""
MessageRouter — Routes inbound Channel messages to GRIP actions.

The router is the brain of GRIP Channel. It inspects each InboundMessage
and decides what to do:
    - /command messages -> execute GRIP skill/command
    - Permission responses -> relay to pending tool approval
    - Text messages -> context injection into active session
    - Media -> future: image analysis, file processing

The router is stateless per-message. Session state lives in GRIP.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Optional

from channel.bridge import InboundMessage, OutboundMessage, MessageType
from channel.security import SenderAllowlist, PermissionLevel

logger = logging.getLogger(__name__)

# GRIP commands that are safe to execute from Channel (read-only or low-risk)
SAFE_COMMANDS = frozenset({
    "/status", "/coffee", "/recall", "/gitgo",
    "/sprint-status", "/grip-doctor", "/mode",
})

# Commands that require ADMIN permission
ADMIN_COMMANDS = frozenset({
    "/save", "/closeout", "/create-pr", "/shipit",
    "/rsi", "/converge", "/evolve", "/broly",
})


@dataclass
class RouteResult:
    """The outcome of routing a message."""
    action: str          # "execute", "permission", "inject", "deny", "ignore"
    command: str = ""    # The GRIP command to execute (if action=execute)
    args: str = ""       # Command arguments
    response: Optional[OutboundMessage] = None  # Immediate response (if any)
    reason: str = ""     # Why this route was chosen (for logging)


class MessageRouter:
    """Routes Channel messages to GRIP actions.

    Enforces permission levels:
        - READ_ONLY: receives updates only, all inbound messages denied
        - COMMANDS: can execute safe commands (read-only GRIP commands)
        - PERMISSION: can approve/deny tool use (RSI permission relay)
        - ADMIN: can execute any command
    """

    def __init__(self, allowlist: SenderAllowlist):
        self._allowlist = allowlist

    def route(self, message: InboundMessage) -> RouteResult:
        """Route an inbound message to a GRIP action.

        Returns a RouteResult describing what to do.
        """
        # Security check
        sender = self._allowlist.check(
            message.sender_id, message.platform.value
        )
        if sender is None:
            return RouteResult(
                action="deny",
                reason=f"Sender {message.sender_id} not in allowlist or rate-limited",
            )

        # READ_ONLY senders cannot send anything actionable
        if sender.permission == PermissionLevel.READ_ONLY:
            return RouteResult(
                action="deny",
                reason=f"Sender {sender.name} has read-only permission",
            )

        # Permission responses (approve/deny tool use)
        if message.is_permission_response:
            if sender.permission.value not in ("permission", "admin"):
                return RouteResult(
                    action="deny",
                    reason=f"Sender {sender.name} lacks permission relay access",
                )
            return RouteResult(
                action="permission",
                command=message.content,
                reason="Permission relay from Channel",
            )

        # Command messages
        if message.is_command:
            return self._route_command(message, sender)

        # Plain text -> context injection
        return RouteResult(
            action="inject",
            command=message.content,
            reason=f"Text from {sender.name} injected as context",
        )

    def _route_command(self, message: InboundMessage, sender) -> RouteResult:
        """Route a /command message."""
        # Parse command and args
        parts = message.content.strip().split(None, 1)
        command = parts[0].lower()
        args = parts[1] if len(parts) > 1 else ""

        # Check permission level
        if command in ADMIN_COMMANDS:
            if sender.permission != PermissionLevel.ADMIN:
                return RouteResult(
                    action="deny",
                    command=command,
                    reason=f"{command} requires admin permission, sender has {sender.permission.value}",
                    response=OutboundMessage(
                        platform=message.platform,
                        recipient_id=message.sender_id,
                        content=f"Denied: {command} requires admin access",
                        reply_to=message.id,
                    ),
                )

        if command in SAFE_COMMANDS or command in ADMIN_COMMANDS:
            return RouteResult(
                action="execute",
                command=command,
                args=args,
                reason=f"Command {command} from {sender.name}",
            )

        # Unknown command
        return RouteResult(
            action="deny",
            command=command,
            reason=f"Unknown command: {command}",
            response=OutboundMessage(
                platform=message.platform,
                recipient_id=message.sender_id,
                content=f"Unknown command: {command}. Try /status or /coffee",
                reply_to=message.id,
            ),
        )
