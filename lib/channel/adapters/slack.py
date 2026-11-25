"""
Slack adapter for GRIP Channel.

Uses Slack Socket Mode (WebSocket) for real-time events and
Web API for sending responses. Replaces polling with push-based
event delivery for lower latency and fewer API calls.

Origin: Issue #1458, Gen 488 RSI Sprint.
"""

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

SLACK_API = "https://slack.com/api"


class SlackAdapter:
    """Slack adapter implementing MessageBridge protocol.

    Uses Slack Socket Mode for real-time events and Web API for sending.
    Requires both a bot token (xoxb-*) and app-level token (xapp-*).
    """

    def __init__(self, bot_token: str = "", app_token: str = ""):
        self._bot_token = bot_token
        self._app_token = app_token
        self._connected = False
        self._ws = None
        self._session = None
        self._bot_user_id: Optional[str] = None

    @property
    def platform(self) -> Platform:
        return Platform.SLACK

    @property
    def is_connected(self) -> bool:
        return self._connected

    async def _open_socket_url(self) -> str:
        """Get WebSocket URL via apps.connections.open."""
        import aiohttp
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{SLACK_API}/apps.connections.open",
                headers={"Authorization": f"Bearer {self._app_token}"},
            ) as resp:
                data = await resp.json()
                if not data.get("ok"):
                    raise ConnectionError(f"Socket Mode open failed: {data.get('error')}")
                return data["url"]

    async def _resolve_bot_id(self) -> None:
        """Resolve bot user ID for self-message filtering."""
        import aiohttp
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{SLACK_API}/auth.test",
                headers={"Authorization": f"Bearer {self._bot_token}"},
            ) as resp:
                auth = await resp.json()
                if auth.get("ok"):
                    self._bot_user_id = auth.get("user_id")

    async def connect(self) -> None:
        """Connect via Slack Socket Mode.

        Socket Mode uses an app-level token to open a WebSocket connection.
        Events are pushed in real-time (no polling required).
        """
        if not self._app_token:
            raise ValueError("Slack app-level token (xapp-*) required for Socket Mode")
        if not self._bot_token:
            raise ValueError("Slack bot token (xoxb-*) required for API calls")

        try:
            import aiohttp
            ws_url = await self._open_socket_url()
            await self._resolve_bot_id()
            self._session = aiohttp.ClientSession()
            self._ws = await self._session.ws_connect(ws_url)
            self._connected = True
            logger.info("Slack Socket Mode connected (bot: %s)", self._bot_user_id)
        except ImportError:
            raise ImportError("aiohttp required for Socket Mode: pip install aiohttp")
        except Exception as e:
            logger.error("Slack Socket Mode connection failed: %s", e)
            self._connected = False
            raise

    async def disconnect(self) -> None:
        """Close Socket Mode WebSocket."""
        if self._ws and not self._ws.closed:
            await self._ws.close()
        if self._session:
            await self._session.close()
            self._session = None
        self._connected = False
        logger.info("Slack Socket Mode disconnected")

    async def send(self, message: OutboundMessage) -> bool:
        """Send via Slack Web API chat.postMessage."""
        if not self._connected or not self._bot_token:
            return False
        channel = message.metadata.get("channel", message.recipient_id)
        if not channel:
            logger.warning("No channel in OutboundMessage for Slack send")
            return False
        try:
            import aiohttp
            payload = {"channel": channel, "text": message.content}
            if message.reply_to:
                payload["thread_ts"] = message.reply_to
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{SLACK_API}/chat.postMessage",
                    headers={"Authorization": f"Bearer {self._bot_token}", "Content-Type": "application/json"},
                    json=payload,
                ) as resp:
                    data = await resp.json()
                    if data.get("ok"):
                        return True
                    logger.warning("Slack send failed: %s", data.get("error"))
                    return False
        except Exception as e:
            logger.error("Slack send error: %s", e)
            return False

    async def acknowledge(self, envelope_id: str) -> None:
        """Acknowledge a Socket Mode event (required within 3s)."""
        if self._ws and not self._ws.closed:
            await self._ws.send_json({"envelope_id": envelope_id})

    @staticmethod
    def _classify_message_type(text: str) -> MessageType:
        """Determine message type from content."""
        if text.startswith("/"):
            return MessageType.COMMAND
        if text.lower() in ("approve", "deny", "yes", "no"):
            return MessageType.PERMISSION
        return MessageType.TEXT

    async def on_message(self, raw: dict) -> Optional[InboundMessage]:
        """Parse a Slack Socket Mode envelope into an InboundMessage."""
        try:
            envelope_id = raw.get("envelope_id")
            if envelope_id:
                await self.acknowledge(envelope_id)

            event = raw.get("payload", raw).get("event", raw)
            if event.get("type") != "message" or "subtype" in event:
                return None
            if event.get("user") == self._bot_user_id:
                return None

            text = event.get("text", "")
            if not text:
                return None

            return InboundMessage(
                id=event.get("ts", ""),
                platform=Platform.SLACK,
                sender_id=event.get("user", ""),
                content=text,
                message_type=self._classify_message_type(text),
                metadata={"channel": event.get("channel", ""), "thread_ts": event.get("thread_ts")},
            )
        except (KeyError, TypeError) as e:
            logger.warning("Failed to parse Slack message: %s", e)
            return None
