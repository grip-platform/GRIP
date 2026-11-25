"""
WhatsApp adapter for GRIP Channel.

The live WhatsApp connection uses Baileys (WhatsApp Web API) via a Node.js
subprocess in the grip-channel daemon. This Python adapter translates the
inbound WhatsApp events that connection emits into GRIP's InboundMessage.

WhatsApp is the primary Channel target because:
    - V>>'s primary communication channel
    - 221 URLs of context from WhatsApp archive scan
    - Permission relay from phone is highest-value use case

Scope: this adapter provides the **inbound parse** path only
(``on_message`` — Baileys event → ``InboundMessage``). The live WhatsApp
connection + outbound send run in the Node.js/Baileys grip-channel daemon,
not in this Python adapter; the connect/send transport is intentionally not
duplicated here.
"""

from __future__ import annotations

import logging
from typing import Optional

from channel.bridge import (
    InboundMessage,
    Platform, MessageType,
)

logger = logging.getLogger(__name__)


class WhatsAppAdapter:
    """WhatsApp inbound-parse adapter.

    Translates raw Baileys WhatsApp events into ``InboundMessage`` via
    ``on_message``. The live connection + outbound send live in the
    Node.js/Baileys grip-channel daemon, not here.
    """

    def __init__(self, phone_number: str = "", session_dir: str = ""):
        self._phone = phone_number
        self._session_dir = session_dir
        self._connected = False

    @property
    def platform(self) -> Platform:
        return Platform.WHATSAPP

    @property
    def is_connected(self) -> bool:
        return self._connected

    async def on_message(self, raw: dict) -> Optional[InboundMessage]:
        """Parse a raw Baileys event into an InboundMessage.

        Baileys emits events like:
        {
            "key": {"remoteJid": "27..@s.whatsapp.net", "id": "..."},
            "message": {"conversation": "text content"},
            "pushName": "Sender Name"
        }
        """
        try:
            key = raw.get("key", {})
            sender_jid = key.get("remoteJid", "")
            message_body = ""
            msg = raw.get("message", {})

            # Extract text from various WhatsApp message types
            if "conversation" in msg:
                message_body = msg["conversation"]
            elif "extendedTextMessage" in msg:
                message_body = msg["extendedTextMessage"].get("text", "")

            if not message_body:
                return None  # Non-text message (media, etc.)

            # Determine message type
            msg_type = MessageType.TEXT
            if message_body.startswith("/"):
                msg_type = MessageType.COMMAND
            elif message_body.lower() in ("approve", "deny", "yes", "no"):
                msg_type = MessageType.PERMISSION

            return InboundMessage(
                id=key.get("id", ""),
                platform=Platform.WHATSAPP,
                sender_id=sender_jid,
                sender_name=raw.get("pushName", ""),
                content=message_body,
                message_type=msg_type,
                metadata={"raw_key": key},
            )
        except (KeyError, TypeError) as e:
            logger.warning("Failed to parse WhatsApp message: %s", e)
            return None
