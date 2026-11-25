"""Workspace-aware Slack transport shim.

Single entry point for posting to any configured Slack workspace. Resolves
the bot token from the macOS keychain, falling back to a per-workspace
environment variable when the keychain is unavailable (Linux hosts have no
``security`` CLI), and posts via the Slack Web API using stdlib only (no
requests dependency).

``workspace`` is trusted-caller configuration, not untrusted input: any
caller able to choose it can trigger a keychain lookup and an env-var read
for the derived name. Callers must not forward unvalidated external text
into ``workspace`` without their own allowlist.

Public API::

    from lib.slack_transport import post

    result = post(
        workspace="codetonight",
        channel_id="C0EXAMPLE01",
        text="Fallback text",
        blocks=[...],          # optional Block Kit blocks
        thread_ts="1234.5678", # optional thread reply
    )
    # result: {ok: bool, ts: str|None, error: str|None, channel: str}

Workspace dispatch
------------------
- ``codetonight`` — keychain service ``grip-codetonight-slack``, then env var
                     ``GRIP_SLACK_BOT_TOKEN``; raises RuntimeError with MCP
                     instructions if neither is present.
- any other name  — derived by convention: keychain service
                     ``grip-<workspace>-slack``, then env var
                     ``<WORKSPACE>_SLACK_BOT_TOKEN`` (workspace name
                     upper-cased, hyphens become underscores). A workspace
                     name must match ``^[a-z][a-z0-9-]*$`` (lower-case,
                     starting with a letter); anything else raises
                     ``ValueError``.

Token resolution ladder (all inside this shim's scope — the token never
appears at any call site, so env-var-protection guardrails and the syscall
doctrine hold by construction):

1. macOS keychain (``security find-generic-password``) — wins if present.
2. Per-workspace environment variable — used only on a keychain miss
   (the production VPS is Linux: no ``security`` CLI, keychain read fails
   cleanly, env var carries the token).

MCP note
--------
The MCP Slack tools (``mcp__slack__*``) live in Claude's tool surface, not in
this Python process. This shim cannot call them. For the CodeTonight workspace,
either (a) store a bot token under ``grip-codetonight-slack`` in the keychain,
(b) set ``GRIP_SLACK_BOT_TOKEN`` in the process environment (Linux/VPS), or
(c) use ``mcp__slack__slack_post_message`` directly in the Claude session.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

__all__ = ["post", "STOP_MARKER"]

# Global outbound brake (V>> STOP override, 2026-06-10): when this marker
# exists, every Slack send from this host is refused — same contract as
# lib/whatsapp_transport.STOP_MARKER. Engage/release via `grip-stop on|off`.
# KNOWN LIMIT (tracked): marker is single-host; VPS-originated daemon sends
# (channel_reply dispatch) are not yet brake-aware.
STOP_MARKER = Path.home() / ".claude" / "state" / "session" / "outbound-stop.json"

_SLACK_API_URL = "https://slack.com/api/chat.postMessage"

# A workspace name must look like this to be accepted at all — lower-case,
# starting with a letter. Every derived keychain/env name below is built
# from a name already known to match this.
_WORKSPACE_NAME_RE = re.compile(r"^[a-z][a-z0-9-]*$")

# Explicit overrides for workspaces whose keychain service or env var does
# NOT follow the `grip-<workspace>-slack` / `<WORKSPACE>_SLACK_BOT_TOKEN`
# convention derived below. Keep this map to the cases that genuinely need
# one — every other accepted workspace name resolves by convention, not
# by a map entry.
_KEYCHAIN = {
    "codetonight": "grip-codetonight-slack",
}
# Keychain-miss fallback (Linux/VPS hosts have no `security` CLI). The token
# is read inside this shim's process scope only — never passed at a call site.
_ENV_VAR = {
    "codetonight": "GRIP_SLACK_BOT_TOKEN",
}


def _resolve_or_derive(workspace: str, explicit: dict[str, str], derive) -> str:
    """Explicit-override lookup with a derived fallback, refusing a collision.

    An EXPLICIT entry is free to not follow the derivation convention (that is
    the whole reason it needs an entry) — ``codetonight``'s env var is
    ``GRIP_SLACK_BOT_TOKEN``, not ``CODETONIGHT_SLACK_BOT_TOKEN``. That freedom
    means a *different*, unlisted workspace can derive the exact same name by
    convention — a workspace literally named ``"grip"`` derives
    ``GRIP_SLACK_BOT_TOKEN`` too (sovereign-council review, 2026-11-22: caught
    by two independent reviewers reading this module cold). Resolving that
    silently would let a post to one workspace reach another's credential.
    Refuse instead: the explicit map is small, so this collision is always
    caught before any token is ever involved, never discovered via a
    mis-delivered message.
    """
    if workspace in explicit:
        return explicit[workspace]
    derived = derive(workspace)
    collision = next((ws for ws, v in explicit.items() if v == derived), None)
    if collision:
        raise ValueError(
            f"Workspace {workspace!r} would derive {derived!r}, which "
            f"collides with the EXPLICIT entry for {collision!r}. Add "
            f"{workspace!r} to the explicit map with a distinct name."
        )
    return derived


def _keychain_service_for(workspace: str) -> str:
    """Keychain service name for a workspace: explicit override, else derived."""
    return _resolve_or_derive(workspace, _KEYCHAIN, lambda ws: f"grip-{ws}-slack")


def _env_var_for(workspace: str) -> str:
    """Env var name for a workspace: explicit override, else derived."""
    return _resolve_or_derive(
        workspace, _ENV_VAR,
        lambda ws: f"{ws.upper().replace('-', '_')}_SLACK_BOT_TOKEN",
    )


def post(
    workspace: str,
    channel_id: str,
    text: str,
    blocks: list[dict] | None = None,
    thread_ts: str | None = None,
) -> dict:
    """Post a Slack message to the given workspace.

    ``workspace`` is ``"codetonight"`` or any other lower-case name matching
    ``^[a-z][a-z0-9-]*$`` — see the module docstring's "Workspace dispatch"
    section for how an unlisted name resolves to a keychain service and env
    var by convention.

    Returns a dict with keys: ``ok`` (bool), ``ts`` (str or None),
    ``error`` (str or None), ``channel`` (str).

    Raises ``ValueError`` for a malformed workspace name.
    Raises ``RuntimeError`` when neither the keychain nor the fallback env
    var yields a token for the workspace (with instructions on the MCP
    alternative for ``codetonight``).
    Never raises on Slack API errors — those are returned as ``{ok: False}``.
    """
    if not _WORKSPACE_NAME_RE.match(workspace):
        raise ValueError(
            f"Malformed workspace name {workspace!r}. "
            "Must match ^[a-z][a-z0-9-]*$ (lower-case, starting with a letter)."
        )

    if STOP_MARKER.exists():
        return {
            "ok": False,
            "ts": None,
            "error": "outbound-stop engaged (release with `grip-stop off`)",
            "channel": channel_id,
        }

    token = _resolve_token(workspace)
    return _call_api(token=token, channel_id=channel_id, text=text,
                     blocks=blocks, thread_ts=thread_ts)


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _resolve_token(workspace: str) -> str:
    """Resolve the bot token for the given workspace.

    Ladder (both rungs inside this shim's scope):
    1. macOS keychain — wins if present.
    2. Per-workspace env var — keychain-miss fallback for Linux/VPS hosts.

    Raises RuntimeError with a clear action message when neither yields a
    token.
    """
    service = _keychain_service_for(workspace)
    token = _keychain_read(service)
    if token:
        return token

    env_var = _env_var_for(workspace)
    token, env_present = _classify_env(env_var)
    if token:
        return token

    # Set-but-empty (operator typo `export VAR=`) vs entirely unset are
    # different failure modes — naming them differently saves the operator
    # from chasing "the variable is missing" when it is actually set blank.
    if env_present:
        env_state = (
            f"environment variable '{env_var}' is set but empty/whitespace "
            f"(an `export {env_var}=` typo — give it the xoxb- token value)"
        )
    else:
        env_state = f"environment variable '{env_var}' is not set"

    if workspace == "codetonight":
        raise RuntimeError(
            f"No CodeTonight Slack token: keychain service '{service}' has "
            f"no entry and {env_state}. Provide one via:\n"
            "  security add-generic-password -s grip-codetonight-slack "
            "-a <account> -w xoxb-...   (macOS)\n"
            f"  export {env_var}=xoxb-...   (Linux/VPS)\n"
            "or use the MCP tool directly in the Claude session:\n"
            "  mcp__slack__slack_post_message(channel_id=..., text=...)"
        )
    raise RuntimeError(
        f"No token: keychain service '{service}' has no entry and "
        f"{env_state}. Provide one via:\n"
        "  security add-generic-password "
        f"-s {service} -a <account> -w xoxb-...   (macOS)\n"
        f"  export {env_var}=xoxb-...   (Linux/VPS)"
    )


def _classify_env(env_var: str) -> tuple[str | None, bool]:
    """Classify a bot-token environment variable.

    Returns ``(token, present)`` where:
    - ``token`` is the stripped value if it is a usable non-blank string,
      else ``None``;
    - ``present`` is ``True`` when the variable exists in the environment
      at all (even if blank), ``False`` when it is entirely unset.

    The ``present`` flag is what lets the caller distinguish a
    ``export VAR=`` operator typo (set, blank) from a missing variable.
    """
    raw = os.environ.get(env_var)
    if raw is None:
        return None, False
    token = raw.strip()
    return (token or None), True


def _env_read(env_var: str) -> str | None:
    """Read a bot token from the process environment. None if unset/blank.

    Retained for backward compatibility; new code uses ``_classify_env``
    which also reports set-but-empty.
    """
    return _classify_env(env_var)[0]


def _keychain_read(service: str) -> str | None:
    """Read a password from the macOS keychain. Returns None on any failure."""
    try:
        result = subprocess.run(
            ["security", "find-generic-password", "-s", service, "-w"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    token = result.stdout.strip()
    return token if result.returncode == 0 and token else None


def _call_api(
    *,
    token: str,
    channel_id: str,
    text: str,
    blocks: list[dict] | None,
    thread_ts: str | None,
) -> dict:
    """POST to chat.postMessage. Always returns the standard result dict."""
    payload: dict = {"channel": channel_id, "text": text}
    if blocks:
        payload["blocks"] = blocks
    if thread_ts:
        payload["thread_ts"] = thread_ts

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        _SLACK_API_URL,
        data=data,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return {"ok": False, "ts": None, "error": f"http_{e.code}", "channel": channel_id}
    except urllib.error.URLError as e:
        return {"ok": False, "ts": None, "error": f"url_error:{e.reason}", "channel": channel_id}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "ts": None, "error": str(e)[:120], "channel": channel_id}

    return {
        "ok": bool(body.get("ok")),
        "ts": body.get("ts") or None,
        "error": body.get("error") or None,
        "channel": body.get("channel") or channel_id,
    }
