"""Session steering dispatcher for /steer command.

Thin router that delegates to existing modules:
- ImprovementQueue for fix/enhance/drain/status/list
- MEMORY.md for TODO tracking
- Broly meta-agent for investigations

Part of GRIP session steering skill (Gen 470).
"""

import json
from pathlib import Path

from improvement_queue import ImprovementQueue
from path_encoding import encode_project_path
from steering_inbox import inbox_collect, inbox_send

CLAUDE_DIR = Path.home() / ".claude"
MEMORY_DIR = CLAUDE_DIR / "projects" / encode_project_path(CLAUDE_DIR) / "memory"
MEMORY_FILE = MEMORY_DIR / "MEMORY.md"
# Where lib/wave_state.py writes it (state_paths.SESSION_WAVE_POSITION).
WAVE_FILE = CLAUDE_DIR / "state" / "session" / "wave-position"


def steer_fix(desc: str, queue: ImprovementQueue = None) -> str:
    """Queue a fix_now item via ImprovementQueue."""
    q = queue or ImprovementQueue()
    item = q.add(
        category="session_steer",
        description=desc,
        classification="fix_now",
    )
    return f"Queued fix_now [{item['id']}]: {desc}"


def steer_enhance(desc: str, queue: ImprovementQueue = None) -> str:
    """Queue a fix_later enhancement via ImprovementQueue."""
    q = queue or ImprovementQueue()
    item = q.add(
        category="session_steer",
        description=desc,
        classification="fix_later",
    )
    return f"Queued enhancement [{item['id']}]: {desc}"


def steer_todo(desc: str, memory_file: Path = None) -> str:
    """Append a TODO item to MEMORY.md ## TODOs section."""
    path = memory_file or MEMORY_FILE
    if not path.exists():
        return f"Error: {path} not found"

    content = path.read_text()
    todo_line = f"- [ ] {desc}\n"

    if "## TODOs" in content:
        # Append after existing TODOs section content
        lines = content.split("\n")
        insert_idx = None
        in_todos = False
        for i, line in enumerate(lines):
            if line.strip() == "## TODOs":
                in_todos = True
                continue
            if in_todos:
                # Find end of TODOs section (next ## heading or EOF)
                if line.startswith("## ") and line.strip() != "## TODOs":
                    insert_idx = i
                    break
        if insert_idx is None:
            # TODOs is the last section — append at end
            lines.append(todo_line.rstrip())
        else:
            lines.insert(insert_idx, todo_line.rstrip())
        content = "\n".join(lines)
    else:
        # Create new TODOs section before the end
        content = content.rstrip() + "\n\n## TODOs\n\n" + todo_line

    path.write_text(content)
    return f"Added TODO: {desc}"


def steer_investigate(query: str) -> str:
    """Return a formatted Broly invocation prompt for the query."""
    return (
        f"Invoke /broly to investigate: {query}\n\n"
        f"Use ReAct framework at FORK power level. "
        f"Run as background agent to avoid disrupting the current loop."
    )


def steer_status(queue: ImprovementQueue = None) -> str:
    """Return queue stats + current wave position."""
    q = queue or ImprovementQueue()
    s = q.stats()

    lines = [
        "## Steer Status",
        "",
        f"Queue: {s['pending']} pending ({s['fix_now']} fix_now, "
        f"{s['fix_later']} fix_later) | "
        f"{s['in_progress']} in-progress | "
        f"{s['completed']} completed | {s['failed']} failed",
    ]

    # Wave position if available
    if WAVE_FILE.exists():
        try:
            wave = json.loads(WAVE_FILE.read_text())
            lines.append(
                f"Wave: position={wave.get('position', '?')} "
                f"phase={wave.get('phase', '?')} "
                f"session={wave.get('session_id', '?')}"
            )
        except (json.JSONDecodeError, OSError):
            lines.append("Wave: unavailable")
    else:
        lines.append("Wave: no active wave")

    return "\n".join(lines)


def steer_list(queue: ImprovementQueue = None) -> str:
    """List all pending items across the queue."""
    q = queue or ImprovementQueue()
    pending = q.list_pending()

    if not pending:
        return "No pending items in queue."

    lines = [f"## Pending Items ({len(pending)})"]
    for item in pending:
        lines.append(
            f"  [{item['classification']}] {item['id']}: {item['description']}"
        )
    return "\n".join(lines)


def steer_inbox_collect() -> list[dict]:
    """Drain the steering inbox and return entries for processing."""
    return inbox_collect()


def steer_inbox_send(
    desc: str,
    source: str = "session_steer",
    priority: str = "normal",
) -> str:
    """Send an entry to the steering inbox."""
    entry = inbox_send(description=desc, source=source, priority=priority)
    return f"Sent to steering inbox: {desc} [{entry.get('ts', '?')}]"


def steer_drain(queue: ImprovementQueue = None) -> list:
    """Drain fix_now items and return them for processing."""
    q = queue or ImprovementQueue()
    items = q.drain_fix_now()
    return items


# CLI interface
if __name__ == "__main__":
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="Session steering dispatcher")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status", help="Queue stats + wave position")
    sub.add_parser("list", help="All pending items")
    sub.add_parser("drain", help="Process fix_now items")

    for name in ("fix", "enhance", "todo", "investigate"):
        p = sub.add_parser(name, help=f"Queue a {name} item")
        p.add_argument("text", nargs="+", help="description")

    sub.add_parser("inbox-collect", help="Drain steering inbox")
    p = sub.add_parser("inbox-send", help="Send to steering inbox")
    p.add_argument("desc", help="entry description")
    p.add_argument("--source", default="session_steer", help="source label")
    p.add_argument(
        "--priority",
        default="normal",
        choices=["normal", "high"],
        help="priority (normal|high)",
    )

    args = parser.parse_args()

    if args.cmd == "fix":
        print(steer_fix(" ".join(args.text)))
    elif args.cmd == "enhance":
        print(steer_enhance(" ".join(args.text)))
    elif args.cmd == "todo":
        print(steer_todo(" ".join(args.text)))
    elif args.cmd == "investigate":
        print(steer_investigate(" ".join(args.text)))
    elif args.cmd == "status":
        print(steer_status())
    elif args.cmd == "list":
        print(steer_list())
    elif args.cmd == "drain":
        items = steer_drain()
        for item in items:
            print(json.dumps(item))
    elif args.cmd == "inbox-collect":
        items = steer_inbox_collect()
        if items:
            for item in items:
                print(json.dumps(item))
        else:
            print("Inbox empty")
    elif args.cmd == "inbox-send":
        if not args.desc.strip():
            print("inbox-send: a description is required", file=sys.stderr)
            raise SystemExit(1)
        print(steer_inbox_send(args.desc, source=args.source, priority=args.priority))
