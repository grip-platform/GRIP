"""
Discord adapter for GRIP Channel.

Uses Discord Bot API via HTTP (no discord.py dependency) for sending and
receives events via Gateway WebSocket connection.

Discord is lower priority since CC ships an official Discord channel plugin,
but this adapter enables GRIP Channel's unified routing across all platforms.

Origin: Issue #1459, Gen 488 RSI Sprint.
"""

import asyncio
import json
import logging
from typing import Optional

from channel.bridge import (
    InboundMessage,
    MessageBridge,
    MessageType,
    OutboundMessage,
    Platform,
)

logger = logging.getLogger(__name__)

DISCORD_API = "https://discord.com/api/v10"
GATEWAY_URL = "wss://gateway.discord.gg/?v=10&encoding=json"


class DiscordAdapter:
    """Discord adapter implementing MessageBridge protocol.

    Uses Discord Gateway (WebSocket) for receiving events and
    REST API for sending messages. Requires a bot token.
    """

    def __init__(self, bot_token: str = "", guild_id: str = ""):
        self._bot_token = bot_token
        self._guild_id = guild_id
        self._connected = False
        self._ws = None
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._session_id: Optional[str] = None
        self._sequence: Optional[int] = None

    @property
    def platform(self) -> Platform:
        return Platform.DISCORD

    @property
    def is_connected(self) -> bool:
        return self._connected

    def _identify_payload(self) -> dict:
        """Build Gateway IDENTIFY payload (opcode 2)."""
        return {
            "op": 2,
            "d": {
                "token": self._bot_token,
                "intents": 512 | 1024,  # GUILD_MESSAGES | MESSAGE_CONTENT
                "properties": {"os": "linux", "browser": "grip-channel", "device": "grip-channel"},
            },
        }

    async def _handshake(self) -> float:
        """Perform HELLO -> IDENTIFY -> READY handshake. Returns heartbeat interval."""
        hello = await self._ws.receive_json()
        interval = hello["d"]["heartbeat_interval"] / 1000.0

        await self._ws.send_json(self._identify_payload())

        ready = await self._ws.receive_json()
        if ready.get("t") != "READY":
            raise ConnectionError(f"Expected READY, got {ready.get('t')}")
        self._session_id = ready["d"]["session_id"]
        return interval

    async def connect(self) -> None:
        """Connect to Discord Gateway via WebSocket."""
        if not self._bot_token:
            raise ValueError("Discord bot token required")
        try:
            import aiohttp
            session = aiohttp.ClientSession()
            self._ws = await session.ws_connect(GATEWAY_URL)
            interval = await self._handshake()
            self._connected = True
            self._heartbeat_task = asyncio.create_task(self._heartbeat_loop(interval))
            logger.info("Discord Gateway connected (session: %s)", self._session_id)
        except ImportError:
            raise ImportError("aiohttp required for Discord Gateway: pip install aiohttp")
        except Exception as e:
            logger.error("Discord connection failed: %s", e)
            self._connected = False
            raise

    async def disconnect(self) -> None:
        """Close Gateway WebSocket and stop heartbeat."""
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            self._heartbeat_task = None
        if self._ws and not self._ws.closed:
            await self._ws.close()
        self._connected = False
        self._session_id = None
        logger.info("Discord Gateway disconnected")

    async def send(self, message: OutboundMessage) -> bool:
        """Send a message via Discord REST API."""
        if not self._connected or not self._bot_token:
            return False

        channel_id = message.metadata.get("channel_id", message.recipient_id)
        if not channel_id:
            logger.warning("No channel_id in OutboundMessage for Discord send")
            return False

        try:
            import aiohttp
            url = f"{DISCORD_API}/channels/{channel_id}/messages"
            headers = {
                "Authorization": f"Bot {self._bot_token}",
                "Content-Type": "application/json",
            }
            payload = {"content": message.content[:2000]}  # Discord 2000 char limit
            if message.reply_to:
                payload["message_reference"] = {"message_id": message.reply_to}

            async with aiohttp.ClientSession() as session:
                async with session.post(url, headers=headers, json=payload) as resp:
                    if resp.status == 200:
                        return True
                    logger.warning("Discord send failed: %s", resp.status)
                    return False
        except Exception as e:
            logger.error("Discord send error: %s", e)
            return False

    @staticmethod
    def _classify_message_type(content: str) -> MessageType:
        """Determine message type from content."""
        if content.startswith("/"):
            return MessageType.COMMAND
        if content.lower() in ("approve", "deny", "yes", "no"):
            return MessageType.PERMISSION
        return MessageType.TEXT

    async def on_message(self, raw: dict) -> Optional[InboundMessage]:
        """Parse a Discord Gateway MESSAGE_CREATE event into InboundMessage."""
        try:
            if raw.get("op") == 0:
                self._sequence = raw.get("s")
            if raw.get("t") != "MESSAGE_CREATE":
                return None

            data = raw.get("d", {})
            author = data.get("author", {})
            if author.get("bot", False):
                return None

            content = data.get("content", "")
            if not content:
                return None

            return InboundMessage(
                id=data.get("id", ""),
                platform=Platform.DISCORD,
                sender_id=author.get("id", ""),
                sender_name=author.get("username", ""),
                content=content,
                message_type=self._classify_message_type(content),
                metadata={"channel_id": data.get("channel_id", ""), "guild_id": data.get("guild_id", "")},
            )
        except (KeyError, TypeError) as e:
            logger.warning("Failed to parse Discord message: %s", e)
            return None

    async def _heartbeat_loop(self, interval: float) -> None:
        """Send periodic heartbeats to keep Gateway alive."""
        while self._connected:
            await asyncio.sleep(interval)
            if self._ws and not self._ws.closed:
                await self._ws.send_json({"op": 1, "d": self._sequence})
