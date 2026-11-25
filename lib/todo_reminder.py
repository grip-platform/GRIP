#!/usr/bin/env python3
"""
GRIP TODO Reminder - Surfaces pending TODOs from MEMORY.md at session start.

Scans all MEMORY.md files across ~/.claude/projects/*/memory/ for the
"GRIP TODO Tracker" markdown table. Outputs a compact [REMINDER] signal
and optionally sends a macOS notification for high item counts.

Usage:
    python3 ~/.claude/lib/todo_reminder.py [--notify]

Flags:
    --notify    Send macOS notification via terminal-notifier or osascript (optional)

Exit code: Always 0 (advisory, never blocks).
"""

import re
import subprocess
import sys
from pathlib import Path


def find_memory_files() -> list[Path]:
    """Find all MEMORY.md files across GRIP project directories."""
    projects_dir = Path.home() / ".claude" / "projects"
    if not projects_dir.is_dir():
        return []
    return sorted(projects_dir.glob("*/memory/MEMORY.md"))


def parse_todo_table(content: str) -> list[dict]:
    """Parse the GRIP TODO Tracker markdown table from MEMORY.md content.

    Expects a table with header: | # | Item | Origin | Category | Notes |
    Returns list of dicts with keys: number, item, origin, category, notes
    """
    todos = []

    # Find the TODO Tracker section
    tracker_match = re.search(
        r"## GRIP TODO Tracker.*?\n\|[^\n]*\|\s*\n\|[-| ]+\|\s*\n((?:\|[^\n]*\|\s*\n)*)",
        content,
        re.DOTALL,
    )
    if not tracker_match:
        return todos

    table_body = tracker_match.group(1)

    for line in table_body.strip().split("\n"):
        line = line.strip()
        if not line or not line.startswith("|"):
            continue

        cells = [c.strip() for c in line.split("|")]
        # Split on | gives empty strings at start/end: ['', '#', 'Item', ...]
        cells = [c for c in cells if c != ""]

        if len(cells) >= 4:
            todos.append(
                {
                    "number": cells[0],
                    "item": cells[1],
                    "origin": cells[2] if len(cells) > 2 else "",
                    "category": cells[3] if len(cells) > 3 else "",
                    "notes": cells[4] if len(cells) > 4 else "",
                }
            )

    return todos


def send_macos_notification(title: str, message: str) -> None:
    """Send a macOS notification.

    Prefers terminal-notifier for click support. Falls back to HAL's osa
    registry, then to direct osascript. Advisory — never raises.
    """
    try:
        import shutil
        if shutil.which("terminal-notifier"):
            subprocess.run(
                ["terminal-notifier", "-title", title, "-message", message,
                 "-sound", "default", "-group", "grip-todo",
                 "-open", "x-apple.terminal://"],
                capture_output=True,
                timeout=5,
            )
            return

        # β-4 preferred fallback: HAL osa registry before raw osascript.
        from lib.hal_osa import notify as hal_notify
        if hal_notify(title, message):
            return

        script = f'display notification "{message}" with title "{title}"'
        subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            timeout=5,
        )
    except Exception:
        pass  # Advisory only, never fail


def main() -> None:
    notify = "--notify" in sys.argv

    memory_files = find_memory_files()
    if not memory_files:
        return

    all_todos: list[dict] = []
    source_count = 0

    for mem_file in memory_files:
        try:
            content = mem_file.read_text(encoding="utf-8")
        except Exception:
            continue

        todos = parse_todo_table(content)
        if todos:
            all_todos.extend(todos)
            source_count += 1

    if not all_todos:
        return

    count = len(all_todos)
    top_item = all_todos[0]["item"]

    # Compact single-line output (Rule 9 compliant)
    print(f'[REMINDER] {count} pending TODO(s) | Top: "{top_item}"')

    # Optional macOS notification for high counts
    if notify and count >= 3:
        send_macos_notification(
            "GRIP TODO Reminder",
            f"{count} pending items. Top: {top_item}",
        )


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # Advisory script -- never crash, never block
        pass
    sys.exit(0)
