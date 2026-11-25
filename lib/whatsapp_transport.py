"""WhatsApp transport shim — local grip-channel MCP bridge.

Single entry point for sending WhatsApp messages from Python to a recipient
JID via the locally-running grip-channel MCP server (default port 3101).

Public API::

    from lib.whatsapp_transport import post

    result = post(
        recipient="27000000000@s.whatsapp.net",
        text="Hello from GRIP",
    )
    # result: {ok: bool, message_id: str|None, error: str|None, recipient: str}

Design
------
grip-channel speaks MCP StreamableHTTP — it does NOT expose a direct REST
endpoint. This shim performs the three-step MCP handshake (initialize ->
notifications/initialized -> tools/call channel_reply) using stdlib only,
hidden behind a workspace-stable function signature mirroring
``lib.slack_transport.post``.

Failure semantics
-----------------
Never raises on transport errors — always returns ``{ok: False, error: ...}``.
Raises ``ValueError`` only for invalid JID format (cheap up-front guard).

Falsifier
---------
This shim is wrong if the MCP handshake fields drift (protocol version
upgrades, session header rename, SSE encoding change). Verifier:
``test_whatsapp_transport.py`` mocks the transport layer, not internals.
"""

from __future__ import annotations

import json
import os
import subprocess
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Optional

__all__ = ["post", "call_tool", "STOP_MARKER"]

# Global outbound brake (V>> STOP override, 2026-06-10): when this marker
# exists, every WhatsApp send is refused. Engage/release via `grip-stop
# on|off` or the spoken "GRIP stop" path in scripts/intel_act_loop.py.
STOP_MARKER = Path.home() / ".claude" / "state" / "session" / "outbound-stop.json"

_DEFAULT_PORT = int(os.environ.get("GRIP_CHANNEL_MCP_PORT", "3101"))
# Host the grip-channel MCP bridge listens on. Defaults to 127.0.0.1 (the Mac
# runs the live WhatsApp Baileys bridge locally). On a host with NO local WA
# bridge — e.g. the VPS, where grip-channel is Slack+Discord only (Hetzner IP
# blocked at the Baileys handshake) — set GRIP_CHANNEL_MCP_HOST to the Mac's
# Tailscale IP to route WA egress over the tailnet to the Mac that holds the
# bridge. Read at import; the relay sets it in the spawn env. The per-call
# `base_url` arg still overrides this for failover routing (see post()).
_DEFAULT_HOST = os.environ.get("GRIP_CHANNEL_MCP_HOST", "127.0.0.1")
_MCP_URL = f"http://{_DEFAULT_HOST}:{_DEFAULT_PORT}/mcp"
_TIMEOUT_S = 15
_PROTOCOL_VERSION = "2024-11-05"
_CLIENT_INFO = {"name": "grip-whatsapp-transport", "version": "1.0.0"}
_HEADERS_BASE = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
}

#: Keychain service holding the grip-channel bearer token.
_TOKEN_SERVICE = "grip-channel-mcp-token"


def _auth_token() -> str:
    """Resolve the grip-channel bearer token, or "" when there is none.

    The daemon gained bearer auth without this shim being updated, so every
    send failed with ``401 Unauthorized`` and the transport had no header to
    offer. Verified 2026-08-22 against the Mac daemon on :3101 — an
    unauthenticated POST returns 401, the same POST with this token returns
    400 (a body complaint, i.e. past the auth gate).

    Resolution order, first non-empty wins:

    1. ``$GRIP_CHANNEL_MCP_TOKEN`` — lets a relay spawn inject it, and is the
       only rung available off macOS, where ``security`` does not exist.
    2. the macOS keychain, read at call time so no secret is ever written to
       disk or held in module state.
    3. "" — no header is sent at all, which preserves the previous behaviour
       against a daemon that does not require auth.

    Never raises, and never logs the value.
    """
    from_env = os.environ.get("GRIP_CHANNEL_MCP_TOKEN", "").strip()
    if from_env:
        return from_env
    try:
        found = subprocess.run(
            ["security", "find-generic-password", "-s", _TOKEN_SERVICE, "-w"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if found.returncode == 0:
            return found.stdout.strip()
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    return ""


# ---------------------------------------------------------------------------
# WhatsApp SSH-egress (syscall-doctrine: ONE shim, host-independent)
# ---------------------------------------------------------------------------
# On a host with NO local WhatsApp bridge (the VPS — the Hetzner IP is blocked
# at the Baileys handshake, so grip-channel there is Slack+Discord only), route
# the SAME shim to the bridge-holder (the second Mac) over an SSH forced-command that
# runs lib/wa_egress_stdin.py — which calls post() LOCALLY there. This folds the
# egress logic that used to live only in relay_dispatcher._wa_ssh_egress INTO
# the shim, so every caller (grip-unlock, the workhorse, the relay) gets correct
# host-independent routing with no duplicated path. Resolution order: env
# GRIP_RELAY_WA_SSH_HOST -> the per-machine gitignored config
# state/relay/wa-egress-host.json. Only a bridge-LESS host carries it; the
# bridge host resolves "" -> local HTTP, never SSH-to-self (no loop). The STOP
# brake + JID validation in post() run BEFORE this branch, so SSH egress is
# equally braked.
_WA_EGRESS_CONFIG = Path.home() / ".claude" / "state" / "relay" / "wa-egress-host.json"


def _resolve_wa_ssh_host() -> str:
    host = os.environ.get("GRIP_RELAY_WA_SSH_HOST", "").strip()
    if host:
        return host
    try:
        if _WA_EGRESS_CONFIG.exists():
            return str(json.loads(_WA_EGRESS_CONFIG.read_text()).get("host", "")).strip()
    except (json.JSONDecodeError, OSError, AttributeError):
        pass
    return ""


def _wa_ssh_key() -> str:
    return os.environ.get(
        "GRIP_RELAY_WA_SSH_KEY", str(Path.home() / ".ssh" / "grip-wa-egress")
    )


def _ssh_egress(recipient: str, text: str, host: str) -> dict:
    """Run the shim remotely on the bridge-holder via SSH forced-command.

    Pipes ``{"recipient","text"}`` JSON over STDIN to wa_egress_stdin.py (the
    text never reaches a shell argv — no injection surface). wa_egress_stdin
    prints the remote post() result dict on stdout for BOTH success and failure,
    so that is the authoritative result. Never raises.
    """
    payload = json.dumps({"recipient": recipient, "text": text})
    try:
        proc = subprocess.run(
            ["ssh", "-i", _wa_ssh_key(), "-o", "BatchMode=yes",
             "-o", "ConnectTimeout=10", "-o", "StrictHostKeyChecking=accept-new",
             host, "wa-egress"],
            input=payload, capture_output=True, text=True, timeout=30,
        )
        out = (proc.stdout or "").strip()
        if out:
            try:
                return json.loads(out)  # authoritative remote post() result
            except json.JSONDecodeError:
                pass
        if proc.returncode == 0:
            return {"ok": True, "message_id": None, "error": None, "recipient": recipient}
        return {
            "ok": False, "message_id": None,
            "error": f"wa_ssh_egress rc={proc.returncode}: {((proc.stderr or '')[:160])}",
            "recipient": recipient,
        }
    except Exception as e:  # noqa: BLE001 — best-effort; mirrors relay egress
        return {
            "ok": False, "message_id": None,
            "error": f"wa_ssh_egress_error: {str(e)[:160]}", "recipient": recipient,
        }


def _http_send(recipient: str, text: str, url: str) -> dict:
    """Local grip-channel MCP bridge — the HTTP transport rung of the shim."""
    try:
        session_id = _initialize(url)
        _notify_initialized(session_id, url)
        return _call_channel_reply(session_id, recipient, text, url)
    except Exception as e:  # noqa: BLE001
        return {
            "ok": False,
            "message_id": None,
            "error": f"transport_error: {str(e)[:160]}",
            "recipient": recipient,
        }


def post(recipient: str, text: str, *, base_url: Optional[str] = None) -> dict:
    """Send a WhatsApp text message via grip-channel MCP.

    Returns a dict with keys: ``ok`` (bool), ``message_id`` (str or None),
    ``error`` (str or None), ``recipient`` (str).

    Raises ``ValueError`` for malformed JID (must contain '@s.whatsapp.net'
    or '@g.us' suffix).

    ``base_url`` selects WHICH grip-channel MCP daemon to hit (e.g. a
    specific relay node's ``http://<host>:3101/mcp``). It defaults to
    ``None``, which resolves to the module-level env-pinned ``_MCP_URL`` —
    so every existing caller is byte-for-byte unchanged. Passing it lets
    ``lib.relay_host`` route to the node its failover ladder selected rather
    than always hitting the local daemon (the per-node-URL contract the
    relay host depends on).
    """
    if not recipient or "@" not in recipient:
        raise ValueError(
            f"Invalid WhatsApp JID {recipient!r}. "
            "Expected '<digits>@s.whatsapp.net' or '<id>@g.us'."
        )

    if STOP_MARKER.exists():
        return {
            "ok": False,
            "message_id": None,
            "error": "outbound-stop engaged (release with `grip-stop off`)",
            "recipient": recipient,
        }

    # syscall-doctrine host-aware routing: NO local bridge on this host -> run
    # the shim on the bridge-holder via SSH. Only for default-routed calls (an
    # explicit base_url is the relay-host failover path -> keep the HTTP path).
    if base_url is None:
        _ssh_host = _resolve_wa_ssh_host()
        if _ssh_host:
            return _ssh_egress(recipient, text, _ssh_host)

    return _http_send(recipient, text, base_url or _MCP_URL)


def call_tool(name: str, arguments: dict) -> str:
    """Invoke an arbitrary grip-channel MCP tool, returning its result text.

    Runs the same three-step handshake as ``post`` (initialize ->
    notifications/initialized -> tools/call ``name``) and returns the first
    text block the tool produced. Used by ``lib.voice_note`` to drive
    ``channel_send_voice`` / ``channel_send_file`` without re-implementing the
    handshake (DRY).

    Does NOT consult ``STOP_MARKER`` — the brake is the caller's responsibility
    (``post`` checks it for text; ``voice_note`` checks it for audio) so this
    stays a thin transport primitive. Raises on transport failure; the caller
    decides how to ladder.
    """
    session_id = _initialize()
    _notify_initialized(session_id)
    payload = {
        "jsonrpc": "2.0",
        "id": uuid.uuid4().hex[:12],
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    }
    _, _, body = _post_json(payload, session_id=session_id)
    return _first_text_block(body)


def _first_text_block(body: bytes) -> str:
    """Extract the first SSE ``result.content[0].text`` from an MCP response."""
    text = body.decode("utf-8", errors="replace")
    for line in text.splitlines():
        if not line.startswith("data: "):
            continue
        try:
            payload = json.loads(line[6:])
        except json.JSONDecodeError:
            continue
        content = payload.get("result", {}).get("content", [])
        if content:
            return str(content[0].get("text", ""))
    return ""


# ---------------------------------------------------------------------------
# Private MCP-over-HTTP helpers (stdlib only)
# ---------------------------------------------------------------------------


def _post_json(
    payload: dict,
    session_id: Optional[str] = None,
    url: Optional[str] = None,
) -> tuple[int, dict, bytes]:
    """POST to /mcp. Returns (status, response_headers, body_bytes).

    ``url`` defaults to the module-level env-pinned ``_MCP_URL`` so existing
    callers are unchanged; ``post(base_url=...)`` threads a per-node URL here.
    """
    headers = dict(_HEADERS_BASE)
    token = _auth_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if session_id:
        headers["Mcp-Session-Id"] = session_id
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url or _MCP_URL, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=_TIMEOUT_S) as resp:
        return resp.status, dict(resp.headers), resp.read()


def _initialize(url: Optional[str] = None) -> str:
    """Open an MCP session. Returns session ID from response header."""
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": _PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": _CLIENT_INFO,
        },
    }
    _, headers, _ = _post_json(payload, url=url)
    sid = headers.get("mcp-session-id") or headers.get("Mcp-Session-Id")
    if not sid:
        raise RuntimeError("grip-channel did not return Mcp-Session-Id")
    return sid


def _notify_initialized(session_id: str, url: Optional[str] = None) -> None:
    """Send the required notifications/initialized completion message."""
    payload = {"jsonrpc": "2.0", "method": "notifications/initialized"}
    _post_json(payload, session_id=session_id, url=url)


def _call_channel_reply(
    session_id: str, recipient: str, text: str, url: Optional[str] = None
) -> dict:
    """Invoke channel_reply tool. Parses SSE response."""
    call_id = uuid.uuid4().hex[:12]
    payload = {
        "jsonrpc": "2.0",
        "id": call_id,
        "method": "tools/call",
        "params": {
            "name": "channel_reply",
            "arguments": {
                "platform": "whatsapp",
                "recipient": recipient,
                "message": text,
            },
        },
    }
    _, _, body = _post_json(payload, session_id=session_id, url=url)
    return _parse_sse_result(body, recipient, call_id)


def _parse_sse_result(body: bytes, recipient: str, call_id: str) -> dict:
    """Parse Server-Sent Events response from MCP. Surfaces tool failures."""
    text = body.decode("utf-8", errors="replace")
    for line in text.splitlines():
        if not line.startswith("data: "):
            continue
        try:
            payload = json.loads(line[6:])
        except json.JSONDecodeError:
            continue
        result = payload.get("result", {})
        content = result.get("content", [])
        first = content[0] if content else {}
        message = first.get("text", "")
        if message.startswith("FAILED:") or "not connected" in message.lower():
            return {
                "ok": False,
                "message_id": None,
                "error": message[:200],
                "recipient": recipient,
            }
        if message.startswith("Sent to whatsapp"):
            return {
                "ok": True,
                "message_id": call_id,
                "error": None,
                "recipient": recipient,
            }
    return {
        "ok": False,
        "message_id": None,
        "error": "no_parsable_sse_response",
        "recipient": recipient,
    }
