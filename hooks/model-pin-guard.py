#!/usr/bin/env python3
"""ConfigChange guard: the shared settings file never keeps a pick the /model picker saved.

Fires on user_settings changes. When config/model-pin.local.json exists, the guard
deletes any top-level ``model`` key from settings.json, and any ``env.ANTHROPIC_MODEL``
entry left from GRIP#8011; the session that pressed Enter keeps its switch, because
Claude Code reads ``model`` once at session start. The pin itself reaches sessions
only through bin/grip's ``--model`` flag; everything GRIP does not launch takes the
account default (the operator's ruling, 2026-10-11). Every revert is one row in
logs/model-pin-guard.jsonl and one terminal notification, since this event discards
systemMessage. With no pin file the guard does nothing. It never blocks and fails open.
Claude Code 2.1.325 sends no ConfigChange for the picker's own save (GRIP#9208), so
bin/grip also runs the same reconcile at each launch, logged with source "launch".

Library: lib/model_pin.py. Tests: tests/test_model_pin.py. Change the default with
bin/grip-model-default, never with the picker's Enter.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
try:
    from lib.model_pin import notice, reconcile
except Exception:  # noqa: BLE001 -- a broken import must never block a session
    sys.exit(0)


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:  # noqa: BLE001
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    if payload.get("source") not in (None, "user_settings"):
        return 0
    try:
        result = reconcile(payload.get("session_id"), source="config_change")
    except Exception:  # noqa: BLE001
        return 0
    if result.get("changed"):
        print(json.dumps(notice(result)))
        print(f"[GRIP] model-pin-guard: kept the default {result.get('pin')}; "
              + "; ".join(result.get("changes") or []), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
