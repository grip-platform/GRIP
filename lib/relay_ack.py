"""Fail-safe acknowledgement sender for the GRIP relay dispatcher.

Sends rung-1 ("Heard") and optional rung-2 ("Still working") acks via the
grip-channel MCP endpoint before the main claude reply (rung-3) lands.

Public API::

    from lib.relay_ack import send_ack

    ok = send_ack(platform="slack", channel_id="C0EXAMPLE01", text="Heard — working on your request — back shortly")

Design contract:
- NEVER raises. All exceptions are swallowed; returns False on failure.
- An ack failure must never block or affect the main reply path.
- Uses the VPS grip-channel MCP endpoint (channel_reply tool) — the same
  transport the claude subprocess uses internally. DRY: one reply path.
- reply_fn is dependency-injected (default = _mcp_channel_reply) so tests
  can inject a fake without network access.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Callable

__all__ = ["send_ack"]

_MCP_URL = os.environ.get(
    "GRIP_RELAY_MCP_URL", "https://your-gateway.example.com/mcp"
)
_ACK_TIMEOUT = int(os.environ.get("GRIP_RELAY_ACK_TIMEOUT", "8"))


def _mcp_channel_reply(platform: str, channel_id: str, text: str) -> bool:
    """Call grip-channel MCP channel_reply via the 3-step JSON-RPC handshake.

    Returns True on success, False on any error (never raises).
    """
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }

    def _post(payload: dict, session_id: str | None = None) -> tuple[dict, str | None]:
        h = dict(headers)
        if session_id:
            h["Mcp-Session-Id"] = session_id
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(_MCP_URL, data=data, headers=h, method="POST")
        with urllib.request.urlopen(req, timeout=_ACK_TIMEOUT) as resp:
            raw = resp.read().decode("utf-8")
            sid = resp.getheader("Mcp-Session-Id")
            for line in raw.splitlines():
                if line.startswith("data:"):
                    raw = line[5:].strip()
                    break
            body = json.loads(raw) if raw.strip().startswith("{") else {}
            return body, sid

    try:
        _, sid = _post(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {},
                    "clientInfo": {"name": "relay-ack", "version": "0.1.0"},
                },
            }
        )
        _post({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}, sid)
        body, _ = _post(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "channel_reply",
                    "arguments": {
                        "platform": platform,
                        "recipient": channel_id,
                        "message": text,
                    },
                },
            },
            sid,
        )
        return "error" not in body
    except Exception:  # noqa: BLE001 — fail-safe: swallow all
        return False


def send_ack(
    platform: str,
    channel_id: str,
    text: str,
    *,
    reply_fn: Callable[[str, str, str], bool] = _mcp_channel_reply,
) -> bool:
    """Send an acknowledgement message. Never raises; returns True on success."""
    try:
        return reply_fn(platform, channel_id, text)
    except Exception:  # noqa: BLE001 — belt-and-suspenders outer guard
        return False
