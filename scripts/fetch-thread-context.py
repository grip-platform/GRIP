#!/usr/bin/env python3
"""
Fetch thread context for Copilot thread replies.

Usage:
    python fetch-thread-context.py --channel C123 --thread-ts 1234567890.123456 --format prompt

Outputs thread history formatted for LLM context injection.
"""

import argparse
import json
import sys
from pathlib import Path

# Add lib to path
sys.path.insert(0, str(Path(__file__).parent.parent / "lib"))

from aide.integrations import SlackAdapter
from aide.thread_registry import CopilotThreadRegistry


def format_for_prompt(messages: list, copilot_context: dict) -> str:
    """Format thread messages as LLM context."""
    lines = []

    # Add original Copilot context
    origin = copilot_context.get("origin", "unknown")
    ritual_id = copilot_context.get("ritual_id", "")
    lines.append(f"This is a reply to a Copilot {origin} message.")
    if ritual_id:
        lines.append(f"Original ritual: {ritual_id}")
    lines.append("")

    # Add conversation history
    lines.append("Previous messages in this thread:")
    lines.append("-" * 40)

    for msg in messages:
        user = msg.get("user", "unknown")
        text = msg.get("text", "")
        bot_id = msg.get("bot_id")

        if bot_id:
            speaker = "GRIP Copilot"
        else:
            speaker = f"User ({user})"

        lines.append(f"{speaker}:")
        lines.append(text)
        lines.append("")

    lines.append("-" * 40)
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Fetch thread context for Copilot replies")
    parser.add_argument("--channel", required=True, help="Slack channel ID")
    parser.add_argument("--thread-ts", required=True, help="Thread timestamp")
    parser.add_argument("--format", choices=["prompt", "json"], default="prompt", help="Output format")
    parser.add_argument("--limit", type=int, default=20, help="Max messages to fetch")
    args = parser.parse_args()

    # Look up Copilot thread metadata
    copilot_context = CopilotThreadRegistry.lookup(args.channel, args.thread_ts) or {}

    # Fetch thread replies
    slack = SlackAdapter()
    if not slack.available():
        print("Slack not available", file=sys.stderr)
        sys.exit(1)

    result = slack.fetch_thread_replies(args.channel, args.thread_ts, limit=args.limit)
    if not result.get("ok"):
        print(f"Failed to fetch thread: {result.get('error')}", file=sys.stderr)
        sys.exit(1)

    messages = result.get("messages", [])

    if args.format == "json":
        print(json.dumps({"messages": messages, "copilot_context": copilot_context}))
    else:
        print(format_for_prompt(messages, copilot_context))


if __name__ == "__main__":
    main()
