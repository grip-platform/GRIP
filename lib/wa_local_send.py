"""Send WhatsApp through the Mac's LOCAL grip-channel daemon.

The grip-channel MCP tools in a Claude session are bound to the VPS daemon,
which is Slack and Discord only by design: WhatsApp refuses the Hetzner IP,
so the WhatsApp bridge lives on the Mac and nowhere else
(``rules/operational-context.md``, the W10 cutover). A session that calls
``channel_reply`` with ``platform="whatsapp"`` therefore gets
"Platform whatsapp not connected" while a perfectly healthy WhatsApp bridge
is running on 127.0.0.1:3101.

This is the local path. It speaks the MCP handshake the daemon requires —
``initialize``, then ``notifications/initialized``, then the call, carrying
the session id the server hands back — because a bare ``tools/call`` is
rejected with "Unknown or missing session ID".

    from lib.wa_local_send import send_whatsapp
    send_whatsapp("27000000000@s.whatsapp.net", "text")

Falsifier
---------
Wrong if the daemon ever accepts a bare tools/call without a session, or if
the VPS gains a WhatsApp bridge — either would make this indirection dead
weight. Until then a session that skips it silently fails to reach the
operator's phone.
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request

__all__ = ["ChannelError", "send_whatsapp", "call_local", "sent_id", "edit_whatsapp"]

ENDPOINT = os.environ.get("GRIP_CHANNEL_LOCAL", "http://127.0.0.1:3101/mcp")
_PROTOCOL = "2025-06-18"


class ChannelError(RuntimeError):
    """The local daemon refused, or could not be reached."""


def _post(payload: dict, session: str | None) -> tuple[dict | None, str | None]:
    token = os.environ.get("GRIP_CHANNEL_MCP_TOKEN", "")
    if not token:
        raise ChannelError("GRIP_CHANNEL_MCP_TOKEN is not set in this environment")
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    if session:
        headers["Mcp-Session-Id"] = session
    req = urllib.request.Request(ENDPOINT, data=json.dumps(payload).encode(),
                                 headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            body = resp.read().decode("utf-8", "replace")
            sid = resp.headers.get("Mcp-Session-Id")
    except urllib.error.URLError as exc:
        raise ChannelError(f"local daemon unreachable at {ENDPOINT}: {exc}") from exc
    # The daemon may answer as SSE; the JSON is on a `data:` line either way.
    for line in body.splitlines():
        line = line[5:].strip() if line.startswith("data:") else line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line), sid
            except json.JSONDecodeError:
                continue
    match = re.search(r"\{.*\}", body, re.S)
    return (json.loads(match.group(0)) if match else None), sid


def call_local(tool: str, arguments: dict) -> dict:
    """One tool call, with the handshake the daemon requires."""
    init, session = _post({
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": _PROTOCOL, "capabilities": {},
                   "clientInfo": {"name": "grip-session", "version": "1.0.0"}},
    }, None)
    if init and init.get("error"):
        raise ChannelError(f"initialize refused: {init['error']}")
    _post({"jsonrpc": "2.0", "method": "notifications/initialized"}, session)
    result, _ = _post({
        "jsonrpc": "2.0", "id": 2, "method": "tools/call",
        "params": {"name": tool, "arguments": arguments},
    }, session)
    if result is None:
        raise ChannelError("no JSON in the daemon's reply")
    if result.get("error"):
        raise ChannelError(str(result["error"]))
    return result


def send_whatsapp(recipient: str, message: str) -> dict:
    return call_local("channel_reply", {"platform": "whatsapp",
                                        "recipient": recipient,
                                        "message": message})


def _reply_text(result):
    """Return the first reply text when it is a string."""
    try:
        text = result["result"]["content"][0]["text"]
    except (KeyError, IndexError, TypeError):
        return None
    return text if isinstance(text, str) else None


def sent_id(result: dict) -> str | None:
    """Extract the message ID from the reply's final id suffix."""
    text = _reply_text(result)
    if text is None:
        return None
    match = re.search(r"(?:^|\s)id=(\S+)\s*$", text)
    return match.group(1) if match else None


def edit_whatsapp(chat: str, message_id: str, text: str) -> dict:
    """Edit a WhatsApp message through the local daemon. Success is only ever the daemon saying
    "Edited message ...": any other reply raises ChannelError with that reply, including an
    older daemon that cannot edit WhatsApp messages and answers about Discord instead."""
    result = call_local("channel_edit", {
        "platform": "whatsapp", "channel_id": chat,
        "message_id": message_id, "new_content": text,
    })
    reply_text = _reply_text(result)
    if reply_text is None or not reply_text.startswith("Edited message"):
        raise ChannelError(reply_text or "no reply text from channel_edit")
    return result
