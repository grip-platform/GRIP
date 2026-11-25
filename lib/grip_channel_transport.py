"""grip-channel transport shim — durable outbound commanding (syscall-doctrine).

Single entry point for posting to Slack / Discord (and WhatsApp, delegated)
from Python, robust against the two failure classes that broke the in-session
MCP tools on 2026-06-25:

1. **Stale MCP session** — Claude Code caches an ``Mcp-Session-Id`` and never
   re-initialises after the grip-channel daemon recycles it (restart / 2h idle
   / pool eviction), so ``mcp__grip-channel__*`` calls fail with
   ``Unknown or missing session ID. Send initialize first.`` (upstream CC bug
   anthropics/claude-code#27142 / #46533). This shim runs a **fresh handshake
   per call** in a fresh process, so it can never hold a stale id; it also
   **re-initialises once** if the daemon invalidates the session mid-call.
2. **VPS ``/mcp`` unreachable** (transient Cloudflare edge / 5xx / conn error)
   — this shim then **fails over to the provider's REST API directly**
   (Discord / Slack). The rung-2 token is resolved in-process — env
   ``GRIP_CHANNEL_DIRECT_TOKEN_<PLATFORM>`` first (so rung 2 works on Linux
   CI / non-macOS hosts with no Keychain), then the macOS Keychain — never
   crossing argv/env to a child (guardrail-safe).

Public API::

    from lib.grip_channel_transport import post

    post("discord", "1519820380813004901", "hello")
    # -> {ok, message_id, error, platform, recipient, via}  (via: 'mcp'|'direct')

Failover ladder (syscall-doctrine, mirrors the HAL provider chain):
    rung 1: VPS grip-channel /mcp  (channel_reply / channel_post_rich)
    rung 2: provider REST direct   (Discord /messages · Slack chat.postMessage)
WhatsApp delegates to ``lib.whatsapp_transport.post`` (the local daemon — a
different boundary, unaffected by the VPS drop).

Failure semantics: never raises on transport errors — always returns
``{ok: False, error: ...}``. ``ValueError`` only for an unknown platform.

Falsifier: wrong if the MCP success-text contract drifts (``Sent to <p>``),
the daemon's session-error JSON-RPC ``error`` shape changes, a provider REST
schema changes, or the rung-2 token is absent from BOTH the env override and
the Keychain (rung 2 then correctly reports a no-token error, never a silent
success). Verifier: ``tests/test_grip_channel_transport.py`` mocks the
transport layer and asserts the 400->reinit->retry path, the /mcp-down->direct
failover, the scoped session-error false-positive guard, the Discord
HTTP-error surface, and the env-token fallback — all by returned
body/recipient, never by call counts (Rule 14 Goodhart guard).
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

__all__ = ["post"]

# Shared outbound brake (V>> STOP override) — same marker whatsapp_transport uses.
STOP_MARKER = Path.home() / ".claude" / "state" / "session" / "outbound-stop.json"

_VPS_MCP_URL = os.environ.get(
    "GRIP_CHANNEL_VPS_MCP", "https://your-gateway.example.com/mcp"
)
_TIMEOUT_S = 15
_PROTOCOL_VERSION = "2024-11-05"
_CLIENT_INFO = {"name": "grip-channel-transport", "version": "1.0.0"}
_HEADERS_BASE = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
}
# Keychain entry per platform for the rung-2 direct REST fallback.
_DIRECT_TOKEN_KC = {"discord": "grip-discord-bot", "slack": "grip-codetonight-slack"}
_SUPPORTED = {"discord", "slack", "whatsapp", "web"}
# Daemon strings that mean "your session is gone, re-initialise" (index.ts:919).
_SESSION_ERR_MARKERS = ("unknown or missing session", "session not found")


class _SessionInvalid(Exception):
    """Raised inside an MCP call when the daemon reports a dead session."""


def post(
    platform: str,
    recipient: str,
    text: str,
    *,
    blocks: Optional[list] = None,
    mcp_url: Optional[str] = None,
) -> dict:
    """Post ``text`` to ``recipient`` on ``platform``, with auto-failover.

    Returns ``{ok, message_id, error, platform, recipient, via}``. ``via`` is
    ``'mcp'`` (rung 1) or ``'direct'`` (rung 2) on success. Never raises on a
    transport failure. ``blocks`` (Slack Block Kit) routes through
    ``channel_post_rich`` / Slack ``chat.postMessage``.
    """
    platform = (platform or "").lower().strip()
    if platform not in _SUPPORTED:
        raise ValueError(f"Unknown platform {platform!r}. Expected one of {sorted(_SUPPORTED)}.")

    if STOP_MARKER.exists():
        return _result(False, None, "outbound-stop engaged (release with `grip-stop off`)",
                       platform, recipient, via=None)

    # WhatsApp is a different boundary (local daemon) — delegate, do not VPS-ladder.
    if platform == "whatsapp":
        try:
            from lib.whatsapp_transport import post as _wa_post  # noqa: PLC0415
            r = _wa_post(recipient, text)
            r.setdefault("platform", "whatsapp")
            r["via"] = "local-daemon"
            return r
        except Exception as e:  # noqa: BLE001
            return _result(False, None, f"whatsapp_delegate_error: {str(e)[:160]}",
                           platform, recipient, via=None)

    # Rung 1: VPS /mcp (with one reinit retry on a dead session).
    mcp_error: Optional[str] = None
    try:
        return _via_mcp(platform, recipient, text, blocks, mcp_url or _VPS_MCP_URL)
    except Exception as e:  # noqa: BLE001 — any /mcp failure drops to rung 2
        mcp_error = str(e)[:160]

    # Rung 2: provider REST direct.
    try:
        return _via_direct(platform, recipient, text, blocks, mcp_error)
    except Exception as e:  # noqa: BLE001
        return _result(False, None, f"mcp+direct both failed: mcp={mcp_error}; direct={str(e)[:160]}",
                       platform, recipient, via=None)


# ---------------------------------------------------------------------------
# Rung 1 — VPS MCP, with single reinit-retry
# ---------------------------------------------------------------------------

def _via_mcp(platform: str, recipient: str, text: str, blocks: Optional[list], url: str) -> dict:
    for attempt in (1, 2):  # second attempt only runs after a _SessionInvalid
        sid = _initialize(url)
        _notify_initialized(sid, url)
        try:
            body = _tool_call(sid, platform, recipient, text, blocks, url)
        except _SessionInvalid:
            if attempt == 2:
                raise RuntimeError("mcp session invalid after reinit")
            continue  # daemon recycled the session — re-initialise and retry once
        return _parse_mcp_result(body, platform, recipient)
    raise RuntimeError("unreachable")  # pragma: no cover


def _tool_call(sid: str, platform: str, recipient: str, text: str,
               blocks: Optional[list], url: str) -> bytes:
    if blocks is not None:
        params = {"name": "channel_post_rich",
                  "arguments": {"platform": platform, "recipient": recipient,
                                "blocks": blocks, "text": text}}
    else:
        params = {"name": "channel_reply",
                  "arguments": {"platform": platform, "recipient": recipient, "message": text}}
    payload = {"jsonrpc": "2.0", "id": uuid.uuid4().hex[:12], "method": "tools/call", "params": params}
    try:
        _, _, body = _post_json(payload, session_id=sid, url=url)
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace").lower()
        if e.code in (400, 404) and any(m in detail for m in _SESSION_ERR_MARKERS):
            raise _SessionInvalid(detail[:160])
        raise  # other HTTP error -> propagate to failover
    if _is_session_error_body(body):  # daemon may also report it in a 200 SSE body
        raise _SessionInvalid("session error in body")
    return body


def _is_session_error_body(body: bytes) -> bool:
    """True iff a 200 SSE body carries a dead-session JSON-RPC *error*.

    Scoped to the JSON-RPC ``error`` field (a string, or an ``{message}``
    object) of each ``data:`` frame — NOT a raw-body substring scan — so a
    legitimate tool *result* whose content merely contains a marker phrase
    (e.g. a help-desk bot forwarding "session not found" from an upstream log)
    is never misclassified as a recycled session and never burns the reinit
    retry. Unparseable / error-less frames are not session errors.
    """
    for line in body.decode("utf-8", errors="replace").splitlines():
        if not line.startswith("data: "):
            continue
        try:
            payload = json.loads(line[6:])
        except json.JSONDecodeError:
            continue
        err = payload.get("error")
        if isinstance(err, dict):
            msg = str(err.get("message", ""))
        elif isinstance(err, str):
            msg = err
        else:
            continue
        if any(m in msg.lower() for m in _SESSION_ERR_MARKERS):
            return True
    return False


def _parse_mcp_result(body: bytes, platform: str, recipient: str) -> dict:
    text = body.decode("utf-8", errors="replace")
    for line in text.splitlines():
        if not line.startswith("data: "):
            continue
        try:
            payload = json.loads(line[6:])
        except json.JSONDecodeError:
            continue
        content = payload.get("result", {}).get("content", [])
        if not content:
            continue  # skip ack/empty SSE frames; the tool result carries content
        msg = content[0].get("text", "")
        if msg.startswith("Sent to"):  # the daemon's confirmed-send contract
            return _result(True, uuid.uuid4().hex[:12], None, platform, recipient, via="mcp")
        # any other content (FAILED:, 'channel not found', etc.) is NOT a confirmed send
        return _result(False, None, (msg[:200] or "unrecognised_mcp_result"), platform, recipient, via="mcp")
    return _result(False, None, "no_parsable_mcp_response", platform, recipient, via="mcp")


# ---------------------------------------------------------------------------
# Rung 2 — provider REST direct (tokens read in-process, never in argv/env)
# ---------------------------------------------------------------------------

def _via_direct(platform: str, recipient: str, text: str,
                blocks: Optional[list], mcp_error: Optional[str]) -> dict:
    token = _direct_token(platform)
    if not token:
        raise RuntimeError(
            f"no token for direct {platform}: set env "
            f"GRIP_CHANNEL_DIRECT_TOKEN_{platform.upper()} or keychain {_DIRECT_TOKEN_KC[platform]!r}")
    if platform == "discord":
        out = _direct_discord(token, recipient, text)
    else:  # slack
        out = _direct_slack(token, recipient, text, blocks)
    if mcp_error:
        out["fallback_from_mcp"] = mcp_error  # context only — never pollutes `error` on success
    return out


def _direct_discord(token: str, channel_id: str, text: str) -> dict:
    body = json.dumps({"content": text}).encode("utf-8")
    req = urllib.request.Request(
        f"https://discord.com/api/v10/channels/{channel_id}/messages",
        data=body, method="POST",
        headers={"Authorization": f"Bot {token}", "Content-Type": "application/json",
                 "User-Agent": "GRIP/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT_S) as r:
            d = json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        # Discord returns real 4xx/5xx (e.g. 401 bad token) — surface the status
        # + body so a failed send is debuggable, not an opaque "both failed".
        detail = e.read().decode("utf-8", errors="replace")[:200]
        return _result(False, None, f"discord_http_{e.code}: {detail}", "discord", channel_id, via="direct")
    return _result(True, str(d.get("id")), None, "discord", channel_id, via="direct")


def _direct_slack(token: str, channel: str, text: str, blocks: Optional[list]) -> dict:
    payload = {"channel": channel, "text": text}
    if blocks is not None:
        payload["blocks"] = blocks
    req = urllib.request.Request(
        "https://slack.com/api/chat.postMessage", data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT_S) as r:
            d = json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        # Parity with _direct_discord: Slack 429/5xx raise HTTPError — surface it.
        detail = e.read().decode("utf-8", errors="replace")[:200]
        return _result(False, None, f"slack_http_{e.code}: {detail}", "slack", channel, via="direct")
    if not d.get("ok"):
        return _result(False, None, f"slack_error: {d.get('error')}", "slack", channel, via="direct")
    return _result(True, str(d.get("ts")), None, "slack", channel, via="direct")


# ---------------------------------------------------------------------------
# Shared MCP-over-HTTP helpers (stdlib only) + keychain
# ---------------------------------------------------------------------------

def _post_json(payload: dict, session_id: Optional[str] = None, url: Optional[str] = None):
    headers = dict(_HEADERS_BASE)
    if session_id:
        headers["Mcp-Session-Id"] = session_id
    req = urllib.request.Request(url or _VPS_MCP_URL, data=json.dumps(payload).encode("utf-8"),
                                 headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=_TIMEOUT_S) as resp:
        return resp.status, dict(resp.headers), resp.read()


def _initialize(url: Optional[str] = None) -> str:
    payload = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
               "params": {"protocolVersion": _PROTOCOL_VERSION, "capabilities": {},
                          "clientInfo": _CLIENT_INFO}}
    _, headers, _ = _post_json(payload, url=url)
    sid = headers.get("mcp-session-id") or headers.get("Mcp-Session-Id")
    if not sid:
        raise RuntimeError("grip-channel did not return Mcp-Session-Id")
    return sid


def _notify_initialized(session_id: str, url: Optional[str] = None) -> None:
    _post_json({"jsonrpc": "2.0", "method": "notifications/initialized"},
               session_id=session_id, url=url)


def _direct_token(platform: str) -> Optional[str]:
    """Resolve the rung-2 REST token for ``platform`` (in-process, never argv/env-to-child).

    Order: env ``GRIP_CHANNEL_DIRECT_TOKEN_<PLATFORM>`` first, so rung 2 is
    exercisable on Linux CI / any non-macOS host without a Keychain; then the
    macOS Keychain entry. ``_keychain`` stays a pure Keychain accessor (SRP) —
    the env override lives here in the resolver, not inside it.
    """
    env = os.environ.get(f"GRIP_CHANNEL_DIRECT_TOKEN_{platform.upper()}")
    if env and env.strip():
        return env.strip()
    return _keychain(_DIRECT_TOKEN_KC[platform])


def _keychain(service: str) -> Optional[str]:
    """macOS Keychain accessor. Returns None off-macOS (``security`` absent) —
    callers must have an env fallback (see ``_direct_token``)."""
    try:
        r = subprocess.run(["security", "find-generic-password", "-s", service, "-w"],
                           capture_output=True, text=True, timeout=5)
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    except Exception:  # noqa: BLE001
        pass
    return None


def _result(ok: bool, message_id: Optional[str], error: Optional[str],
            platform: str, recipient: str, *, via: Optional[str]) -> dict:
    return {"ok": ok, "message_id": message_id, "error": error,
            "platform": platform, "recipient": recipient, "via": via}
