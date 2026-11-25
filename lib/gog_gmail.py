#!/usr/bin/env python3
"""Google Workspace Gmail CLI backed by GRIP's DWD core.

This is the narrow helper expected by ``lib.aide.absorb.gog``. It lists
metadata/snippets by default so inbox triage can run without pulling full bodies.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict

from lib.meet_absorb_dwd import GmailMessage, list_gmail_messages


def _message_dict(message: GmailMessage) -> dict:
    data = asdict(message)
    data["id"] = data.pop("message_id")
    data["from"] = data.pop("sender")
    data["to"] = list(data.pop("recipients"))
    return data


def _list(args: argparse.Namespace) -> dict:
    messages = list_gmail_messages(
        args.user,
        hours_back=args.hours_back,
        limit=args.limit,
        labels=args.label,
    )
    return {
        "user": args.user,
        "hours_back": args.hours_back,
        "messages": [_message_dict(message) for message in messages],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)

    list_parser = sub.add_parser("list", help="List recent Gmail message summaries")
    list_parser.add_argument("--user", required=True, help="Workspace user email to impersonate")
    list_parser.add_argument("--hours-back", type=int, default=24)
    list_parser.add_argument("--limit", type=int, default=20)
    list_parser.add_argument(
        "--label",
        action="append",
        default=["INBOX", "UNREAD"],
        help="Gmail label/query scope; repeatable. Defaults to INBOX + UNREAD.",
    )
    list_parser.add_argument("--format", choices=("json",), default="json")
    list_parser.set_defaults(func=_list)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        payload = args.func(args)
    except Exception as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 1
    print(json.dumps(payload, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
