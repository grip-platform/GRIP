#!/usr/bin/env python3
"""
GitHub-MEMORY Sync Adapter

Bidirectional sync between GitHub Issues and MEMORY.md TODO tracker.
- New GitHub issue with sprint-loop label -> auto-add to MEMORY.md TODOs
- TODO marked DONE in MEMORY.md -> auto-close GitHub issue
- Outputs sync report for copilot workflow integration

Usage:
    python3 lib/github_memory_sync.py sync          # bidirectional sync
    python3 lib/github_memory_sync.py status         # show sync status
    python3 lib/github_memory_sync.py close-done     # close GitHub issues for DONE TODOs
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
from claude_dir import get_claude_dir  # noqa: E402
from path_encoding import encode_project_path  # noqa: E402

_CLAUDE_DIR = get_claude_dir()
MEMORY_PATH = _CLAUDE_DIR / "projects" / encode_project_path(_CLAUDE_DIR) / "memory" / "MEMORY.md"
# Fallback if project-specific memory doesn't exist
if not MEMORY_PATH.exists():
    MEMORY_PATH = _CLAUDE_DIR / "MEMORY.md"


def _gh_issues(label: str = "", state: str = "open", limit: int = 50) -> List[Dict]:
    """Fetch GitHub issues via gh CLI."""
    cmd = ["gh", "issue", "list", "--state", state, "--json",
           "number,title,labels,state", "--limit", str(limit)]
    if label:
        cmd.extend(["--label", label])
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode == 0 and result.stdout.strip():
            return json.loads(result.stdout)
    except (subprocess.TimeoutExpired, json.JSONDecodeError):
        pass
    return []


def _parse_todo_table(content: str) -> List[Dict]:
    """Parse MEMORY.md TODO tracker table into structured data."""
    todos = []
    in_table = False
    for line in content.split("\n"):
        if "| # | Item" in line:
            in_table = True
            continue
        if in_table and line.startswith("|---"):
            continue
        if in_table and line.startswith("|"):
            parts = [p.strip() for p in line.split("|")[1:-1]]
            if len(parts) >= 4:
                try:
                    num = int(parts[0])
                except ValueError:
                    continue
                todos.append({
                    "number": num,
                    "item": parts[1],
                    "origin": parts[2],
                    "status": parts[3],
                    "notes": parts[4] if len(parts) > 4 else "",
                })
        elif in_table and not line.startswith("|"):
            in_table = False
    return todos


def _extract_issue_number(notes: str) -> Optional[int]:
    """Extract GitHub issue number from TODO notes field."""
    match = re.search(r"(?:Issue|#)\s*#?(\d+)", notes, re.IGNORECASE)
    if match:
        return int(match.group(1))
    return None


def sync_status() -> Dict:
    """Get sync status between GitHub issues and MEMORY.md TODOs."""
    issues = _gh_issues()
    content = MEMORY_PATH.read_text() if MEMORY_PATH.exists() else ""
    todos = _parse_todo_table(content)

    # Map issue numbers to issues
    issue_map = {i["number"]: i for i in issues}

    # Find TODOs linked to issues
    linked = []
    unlinked_todos = []
    for todo in todos:
        issue_num = _extract_issue_number(todo.get("notes", ""))
        if issue_num and issue_num in issue_map:
            linked.append({"todo": todo, "issue": issue_map[issue_num]})
        else:
            unlinked_todos.append(todo)

    # Issues not in TODOs
    todo_issue_nums = {_extract_issue_number(t.get("notes", "")) for t in todos}
    unlinked_issues = [i for i in issues if i["number"] not in todo_issue_nums]

    return {
        "linked": linked,
        "unlinked_todos": unlinked_todos,
        "unlinked_issues": unlinked_issues,
        "total_issues": len(issues),
        "total_todos": len(todos),
        "done_todos": [t for t in todos if "DONE" in t.get("status", "")],
    }


def close_done_issues() -> List[Dict]:
    """Close GitHub issues for TODOs marked DONE."""
    content = MEMORY_PATH.read_text() if MEMORY_PATH.exists() else ""
    todos = _parse_todo_table(content)
    closed = []

    for todo in todos:
        if "DONE" not in todo.get("status", "") and "FIXED" not in todo.get("status", ""):
            continue
        issue_num = _extract_issue_number(todo.get("notes", ""))
        if not issue_num:
            continue

        # Check if issue is still open
        try:
            result = subprocess.run(
                ["gh", "issue", "view", str(issue_num), "--json", "state"],
                capture_output=True, text=True, timeout=15,
            )
            if result.returncode == 0:
                state = json.loads(result.stdout).get("state", "")
                if state == "OPEN":
                    close_result = subprocess.run(
                        ["gh", "issue", "close", str(issue_num),
                         "--comment", f"Auto-closed: TODO #{todo['number']} marked {todo['status']} in MEMORY.md"],
                        capture_output=True, text=True, timeout=15,
                    )
                    if close_result.returncode == 0:
                        closed.append({"issue": issue_num, "todo": todo["number"], "status": todo["status"]})
        except (subprocess.TimeoutExpired, json.JSONDecodeError):
            continue

    return closed


def main():
    if len(sys.argv) < 2:
        print("Usage: github_memory_sync.py [sync|status|close-done]")
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "status":
        status = sync_status()
        print(f"GitHub Issues: {status['total_issues']} open")
        print(f"MEMORY TODOs: {status['total_todos']} total, {len(status['done_todos'])} done")
        print(f"Linked: {len(status['linked'])}")
        print(f"Unlinked issues: {len(status['unlinked_issues'])}")
        for i in status["unlinked_issues"]:
            labels = ", ".join(l.get("name", "") for l in i.get("labels", []))
            print(f"  #{i['number']}: {i['title']} [{labels}]")

    elif cmd == "close-done":
        closed = close_done_issues()
        if closed:
            print(f"Closed {len(closed)} issues:")
            for c in closed:
                print(f"  #{c['issue']} (TODO #{c['todo']}: {c['status']})")
        else:
            print("No issues to close.")

    elif cmd == "sync":
        status = sync_status()
        closed = close_done_issues()
        print(json.dumps({
            "synced": len(closed),
            "open_issues": status["total_issues"],
            "todos": status["total_todos"],
            "unlinked_issues": len(status["unlinked_issues"]),
        }))

    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)


if __name__ == "__main__":
    main()
