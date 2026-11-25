#!/usr/bin/env python3
"""daily-tip-channel — post GRIP's tip of the day to #daily-chat.

Replaces the Aide digests in #daily-chat (channel C0EXAMPLE01) with the
tip-of-the-day. Draws from the SAME renderer as the dojo (lib/dojo_tip): a
fresh learned tip when GRIP has grown a new capability, otherwise the curated
tip of the day. Advances the learned-scan cursor so a learned tip is not
re-shown every day.

Slack token is read from the macOS keychain (service ``grip-slack-bot``) — the
same source lib/learning_detector.py uses. No token is ever stored in a file.

Usage:
    daily-tip-channel           # post to #daily-chat
    daily-tip-channel --dry-run # print the tip without posting
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request

CLAUDE_DIR = os.environ.get("CLAUDE_DIR") or os.path.expanduser("~/.claude")
if CLAUDE_DIR not in sys.path:
    sys.path.insert(0, CLAUDE_DIR)

CHANNEL_ID = "C0EXAMPLE01"  # #daily-chat, codetonight workspace
KEYCHAIN_SERVICE = "grip-slack-bot"


def read_token() -> str:
    try:
        return subprocess.check_output(
            ["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-w"],
            stderr=subprocess.DEVNULL, timeout=5,
        ).decode().strip()
    except Exception:
        return ""


def post(text: str, token: str) -> "str | None":
    """Post *text* to #daily-chat. None on success, else why it failed: Slack's
    own error code (for example "not_in_channel"), an HTTP status, or a network
    error. Never the token. On 2026-11-24 a failed run logged only "Slack post
    failed", so its cause could not be found afterwards (GRIP#9380)."""
    payload = json.dumps({"channel": CHANNEL_ID, "text": text}).encode("utf-8")
    req = urllib.request.Request(
        "https://slack.com/api/chat.postMessage",
        data=payload,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
        },
    )
    try:
        resp = urllib.request.urlopen(req, timeout=30)
        result = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return f"HTTP {exc.code}"
    except urllib.error.URLError as exc:
        return f"network error: {type(exc.reason).__name__}"
    except ValueError:
        return "a reply that is not JSON"
    return None if result.get("ok") else str(result.get("error") or "no error code in the reply")


def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(description="Post GRIP's tip of the day to #daily-chat")
    ap.add_argument("--dry-run", action="store_true", help="print the tip without posting")
    args = ap.parse_args(argv)

    from lib.dojo_tip import pick_tip, tip_slack_text

    tip, source = pick_tip(advance=not args.dry_run)
    if not tip:
        print("[daily-tip-channel] no tip available")
        return 0

    text = tip_slack_text(tip, source=source)
    print(f"[daily-tip-channel] source={source} topic={tip.get('topic')}")

    if args.dry_run:
        print(text)
        return 0

    token = read_token()
    if not token:
        print("[daily-tip-channel] no Slack token in keychain; not posting", file=sys.stderr)
        return 1

    error = post(text, token)
    if error is None:
        print("[daily-tip-channel] posted to #daily-chat")
        return 0
    print(f"[daily-tip-channel] Slack post failed: {error}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
