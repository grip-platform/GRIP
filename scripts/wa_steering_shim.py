#!/usr/bin/env python3
"""WhatsApp steering shim for AFK GRIP runs.

Polls V>>'s local WhatsApp history for inbound messages prefixed ``STEER:`` and
appends them to the durable steering inbox. This composes existing surfaces:
grip-channel records history; steering_inbox owns safe JSONL delivery.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "lib"
for path in (ROOT, LIB):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from steering_inbox import inbox_send  # noqa: E402
from wa_identities import MISSING_HINT, operator_jid  # noqa: E402

_OPERATOR = operator_jid()
# The operator's own thread, named the way grip-channel names history files; None
# when no private identities file is configured (lib/wa_identities.py).
DEFAULT_HISTORY = (ROOT / "state" / "whatsapp-history" / f"{_OPERATOR.replace('@', '_')}.jsonl"
                   if _OPERATOR else None)
DEFAULT_CURSOR = ROOT / "state" / "session" / "wa-steering-shim.cursor"


def _load_cursor(path: Path) -> int:
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return 0


def _save_cursor(path: Path, offset: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(offset), encoding="utf-8")


def _handle_row(row: dict) -> bool:
    if row.get("direction") != "in":
        return False
    text = str(row.get("text") or "").strip()
    if not text.upper().startswith("STEER:"):
        return False
    desc = text.split(":", 1)[1].strip()
    if not desc:
        return False
    inbox_send(
        description=desc,
        source="whatsapp-steering-shim",
        instance_id=str(row.get("messageId") or row.get("ts") or ""),
        priority="high",
    )
    return True


def poll_once(history: Path, cursor: Path) -> int:
    offset = _load_cursor(cursor)
    if not history.exists():
        return 0
    sent = 0
    with history.open("rb") as handle:
        handle.seek(offset)
        for raw in handle:
            try:
                row = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            if _handle_row(row):
                sent += 1
        _save_cursor(cursor, handle.tell())
    return sent


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--history", type=Path, default=DEFAULT_HISTORY)
    parser.add_argument("--cursor", type=Path, default=DEFAULT_CURSOR)
    parser.add_argument("--interval", type=float, default=15.0)
    parser.add_argument("--duration", type=float, default=4 * 3600.0)
    args = parser.parse_args(argv)
    if args.history is None:
        parser.error(f"{MISSING_HINT}, or pass --history")

    deadline = time.monotonic() + max(0.0, args.duration)
    total = 0
    while time.monotonic() <= deadline:
        total += poll_once(args.history, args.cursor)
        time.sleep(max(1.0, args.interval))
    print(json.dumps({"steering_items": total, "history": str(args.history)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
