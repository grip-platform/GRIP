"""Mobile steering channel — phone-side steering inbox adapter.

Accepts a steering command from a mobile-facing surface (WhatsApp, Slack DM)
and writes it as a structured entry to the cross-session steering inbox.
Every entry is auditable, priority-aware, and survives concurrent sessions.

Usage (CLI — for hooks / transport scripts)::

    python3 lib/steer_mobile.py send "fix the null deref in server.py"
    python3 lib/steer_mobile.py send --priority high "investigate OOM on prod"
    python3 lib/steer_mobile.py peek
    python3 lib/steer_mobile.py status

Usage (Python — for skill integration)::

    from steer_mobile import steer_mobile_send
    steer_mobile_send("fix the null deref", source="whatsapp")

Falsifier: wrong if ``inbox_send`` contract changes (schema version bump,
priority validation drift) or the CLI arg parser diverges from the Python API.
Verifier: ``tests/test_steer_mobile.py`` asserts entries reach the inbox with
correct schema, priority, and source tagging.
"""

from __future__ import annotations

import argparse
import json


def steer_mobile_send(
    description: str,
    priority: str = "normal",
    source: str = "mobile",
) -> dict:
    """Send a steering item from a mobile surface to the steering inbox.

    Args:
        description: Steering command (e.g. "fix the login redirect bug").
        priority: ``"normal"`` or ``"high"``.
        source: Origin surface identifier (e.g. ``"whatsapp"``, ``"slack"``).

    Returns:
        The entry dict that was written to the inbox.
    """
    from steering_inbox import inbox_send

    return inbox_send(
        description=description,
        source=source,
        instance_id="mobile_steer",
        priority=priority,
    )


def _cmd_send(args: argparse.Namespace) -> None:
    entry = steer_mobile_send(
        description=args.description,
        priority=args.priority,
        source=args.source,
    )
    print(json.dumps(entry, indent=2))


def _cmd_peek(_args: argparse.Namespace) -> None:
    from steering_inbox import inbox_peek
    entries = inbox_peek()
    if not entries:
        print("steering inbox: empty")
        return
    for e in entries:
        marker = "[HIGH] " if e.get("priority") == "high" else ""
        print(f"  {marker}{e['ts'][:19]}  {e['source']:12s}  {e['description']}")


def _cmd_status(_args: argparse.Namespace) -> None:
    from steering_inbox import inbox_peek, INBOX_FILE
    entries = inbox_peek()
    count = len(entries)
    high = sum(1 for e in entries if e.get("priority") == "high")
    print(f"steering inbox: {count} pending ({high} high)")
    print(f"  file: {INBOX_FILE}")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="steer_mobile",
        description="Mobile steering channel — queue steering items from phone surfaces.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    send_parser = sub.add_parser("send", help="Send a steering item to the inbox")
    send_parser.add_argument("description", help="Steering command text")
    send_parser.add_argument(
        "--priority",
        choices=["normal", "high"],
        default="normal",
        help="Entry priority",
    )
    send_parser.add_argument(
        "--source",
        default="mobile",
        help="Origin surface (whatsapp, slack, ...)",
    )

    sub.add_parser("peek", help="List pending steering items")
    sub.add_parser("status", help="Show inbox status")

    parsed = parser.parse_args(argv)

    if parsed.command == "send":
        _cmd_send(parsed)
    elif parsed.command == "peek":
        _cmd_peek(parsed)
    elif parsed.command == "status":
        _cmd_status(parsed)


if __name__ == "__main__":
    main()
