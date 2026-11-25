#!/usr/bin/env python3
"""Slack Socket Mode daemon for the AIDE Oracle.

GRIP's AIDE system posts prediction digests to Slack. A human taps
"Confirmed" or "Falsified" on a prediction message; this daemon listens
for those button taps over Slack Socket Mode, grades the prediction in
the JSONL registry via PredictionRegistry.grade, and replies in-thread
through the interactive response_url.

Design: stdlib-first, one async loop that ACKs every envelope and hands
only "interactive" events to the grader. The grading + reply work runs in
a worker thread (asyncio.to_thread) so the socket loop never blocks on
urllib. WebSocket drops are reconnected with exponential backoff capped at
30s; Slack Socket Mode URLs expire, so a fresh URL is fetched from
apps.connections.open before every connect.
"""

import asyncio
import json
import logging
import os
import subprocess
import sys
import urllib.error
import urllib.request

# Make "from lib.aide.adaptive_engine import ..." resolvable regardless of
# how this file is launched. CLAUDE_DIR/lib is a namespace package (no
# __init__.py), so a regular "lib" package in the current working directory
# (e.g. HAL's ~/.hal/lib) would shadow it whenever "" is on sys.path. This
# daemon only needs CLAUDE_DIR plus site-packages, so drop the cwd entry.
# CLAUDE_DIR is env-driven (Mac launchd and VPS pm2 both set it); the default
# is the operator home so a bare `python3 lib/aide/slack_oracle.py` still works.
CLAUDE_DIR = os.environ.get("CLAUDE_DIR", os.path.expanduser("~/.claude"))
sys.path[:] = [p for p in sys.path if p != ""]
if CLAUDE_DIR not in sys.path:
    sys.path.insert(0, CLAUDE_DIR)

import websockets  # noqa: E402  (after sys.path setup)

from lib.aide.adaptive_engine import PredictionRegistry  # noqa: E402

SLACK_CONNECT_ENDPOINT = "https://slack.com/api/apps.connections.open"
KEYCHAIN_APP_TOKEN_SERVICE = "grip-codetonight-slack-app"
APP_TOKEN_ENV = "GRIP_SLACK_APP_TOKEN"  # Linux/VPS fallback (no keychain there)

GRADE_ACTION_IDS = {"grade_confirmed", "grade_falsified"}
BASE_RECONNECT_DELAY = 1.0
MAX_RECONNECT_DELAY = 30.0
WS_MAX_MESSAGE_SIZE = 4 * 1024 * 1024  # Slack envelopes can be large

logger = logging.getLogger("slack_oracle")


# ----- keychain ------------------------------------------------------------

def read_app_token() -> str:
    """Read the Socket Mode app token (xapp-...).

    Resolution ladder (same discipline as lib/slack_transport.py):
    1. macOS keychain — wins if present (Mac/launchd).
    2. Env var GRIP_SLACK_APP_TOKEN — used on Linux/VPS where the `security`
       CLI does not exist.

    The token is returned in memory only — never logged, never written to disk.
    """
    # 1. keychain (macOS)
    try:
        proc = subprocess.run(
            ["security", "find-generic-password",
             "-s", KEYCHAIN_APP_TOKEN_SERVICE, "-w"],
            capture_output=True,
            check=True,
            text=True,
        )
        token = proc.stdout.strip()
        if token:
            return token
    except (subprocess.CalledProcessError, OSError):
        pass  # not macOS, or keychain entry missing → fall through to env

    # 2. env var (Linux/VPS)
    token = os.environ.get(APP_TOKEN_ENV, "").strip()
    if token:
        return token

    raise RuntimeError(
        f"no Slack app token: keychain service '{KEYCHAIN_APP_TOKEN_SERVICE}' "
        f"missing AND env var {APP_TOKEN_ENV} unset"
    )


# ----- Slack HTTP (stdlib urllib) ------------------------------------------

def fetch_socket_url(app_token: str) -> str:
    """POST apps.connections.open and return the wss:// socket-mode URL."""
    request = urllib.request.Request(
        SLACK_CONNECT_ENDPOINT,
        data=b"",  # form-urlencoded body; Slack accepts an empty payload
        headers={
            "Authorization": f"Bearer {app_token}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        body = response.read().decode("utf-8", errors="replace")
    try:
        data = json.loads(body)
    except ValueError as exc:
        raise RuntimeError("apps.connections.open returned non-JSON") from exc
    if not data.get("ok"):
        raise RuntimeError(
            "apps.connections.open failed: "
            f"{data.get('error', 'unknown_error')}"
        )
    url = data.get("url")
    if not url:
        raise RuntimeError("apps.connections.open returned ok but no url")
    return url


def post_reply(response_url: str, text: str) -> None:
    """Reply in-thread to an interactive event via its response_url."""
    payload = json.dumps(
        {"text": text, "replace_original": False}
    ).encode("utf-8")
    request = urllib.request.Request(
        response_url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            response.read()
    except Exception as exc:  # network/HTTP error — log, never crash
        logger.warning("failed to post reply to response_url: %s", exc)


# ----- interactive handling ------------------------------------------------

def handle_interactive(payload: dict) -> None:
    """Grade a prediction from a Slack button tap and reply in-thread."""
    actions = payload.get("actions") or []
    action = next(
        (a for a in actions if a.get("action_id") in GRADE_ACTION_IDS),
        None,
    )
    if action is None:
        logger.info("ignoring interactive event with no grade action")
        return

    action_id = action.get("action_id")
    pid = action.get("value")
    user_id = (payload.get("user") or {}).get("id")
    response_url = payload.get("response_url")
    if not pid or not response_url:
        logger.warning("interactive event missing pid or response_url; ignoring")
        return

    outcome = "confirmed" if action_id == "grade_confirmed" else "falsified"
    try:
        registry = PredictionRegistry()
        ok = registry.grade(
            pid,
            outcome=outcome,
            evidence=[f"human-graded via Slack by {user_id}"],
        )
    except Exception as exc:
        logger.error("failed to grade prediction %s: %s", pid, exc)
        post_reply(
            response_url,
            f":x: Couldn't grade {pid} — internal error, please try again.",
        )
        return

    if ok:
        text = f":white_check_mark: Graded {pid} as {outcome} — thanks! The loop learns."
    else:
        text = f":grey_exclamation: {pid} not found or already graded."
    post_reply(response_url, text)
    logger.info(
        "grade outcome for %s (%s by %s): %s", pid, outcome, user_id, "ok" if ok else "noop"
    )


# ----- socket loop ---------------------------------------------------------

async def handle_envelope(ws, raw: str) -> None:
    """Parse one envelope, ACK it, and act on interactive events."""
    try:
        envelope = json.loads(raw)
    except (TypeError, ValueError) as exc:
        logger.warning("dropping malformed event: %s", exc)
        return

    envelope_id = envelope.get("envelope_id")
    if envelope_id is None:
        logger.warning("event without envelope_id; ignoring")
        return

    # Slack requires an ACK for every envelope.
    try:
        await ws.send(json.dumps({"envelope_id": envelope_id}))
    except Exception as exc:
        logger.warning("failed to ACK envelope %s: %s", envelope_id, exc)

    if envelope.get("type") != "interactive":
        return

    payload = envelope.get("payload")
    if not isinstance(payload, dict):
        logger.warning("interactive event without a payload dict")
        return

    # Grading + reply use blocking urllib; run them off the event loop so
    # the socket keeps ACKing promptly.
    try:
        await asyncio.to_thread(handle_interactive, payload)
    except Exception as exc:
        logger.exception("error handling interactive event: %s", exc)


async def consume(ws) -> None:
    """Receive envelopes until the connection closes."""
    async for raw in ws:
        await handle_envelope(ws, raw)


async def run_socket(app_token: str) -> None:
    """Connect, consume, and reconnect with exponential backoff (max 30s)."""
    delay = BASE_RECONNECT_DELAY
    while True:
        try:
            url = fetch_socket_url(app_token)
            logger.info("opening socket-mode connection")
            async with websockets.connect(
                url,
                max_size=WS_MAX_MESSAGE_SIZE,
                ping_interval=20,
                ping_timeout=20,
            ) as ws:
                delay = BASE_RECONNECT_DELAY  # healthy connection: reset backoff
                logger.info("socket connected")
                await consume(ws)
            logger.warning("socket closed by server")
        except KeyboardInterrupt:
            raise
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("socket error (%s); reconnecting in %.1fs", exc, delay)
            await asyncio.sleep(delay)
            delay = min(delay * 2, MAX_RECONNECT_DELAY)


# ----- entry point ---------------------------------------------------------

def main() -> None:
    logging.basicConfig(
        stream=sys.stderr,
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        app_token = read_app_token()
    except RuntimeError as exc:
        logger.error("%s", exc)
        sys.exit(1)
    logger.info("AIDE Oracle Slack daemon starting")
    try:
        asyncio.run(run_socket(app_token))
    except KeyboardInterrupt:
        logger.info("interrupted; shutting down gracefully")
    except Exception as exc:
        logger.exception("fatal error: %s", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
