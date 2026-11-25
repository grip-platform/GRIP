"""
Backlog Scorer for GRIP MEMORY.md TODO items.

Parses the TODO table from MEMORY.md and scores each item on 5 dimensions
(0-20 each, max 100) to enable priority-based Kanban assignment.

Dimensions:
  1. Impact (20)  - User-facing features > infra > meta-work
  2. Urgency (20) - BLOCKED/P0 > IN_ISSUE > PENDING
  3. Effort (20)  - Inverse: quick wins score higher
  4. Dependencies (20) - Items with fewer blockers score higher
  5. Staleness (20) - Older items with no progress get priority bump

Usage:
    python3 lib/backlog_scorer.py               # Print ranked backlog
    python3 lib/backlog_scorer.py --json         # JSON output
    python3 lib/backlog_scorer.py --kanban       # Kanban column assignment
    python3 lib/backlog_scorer.py --html         # Regenerate backlog-dashboard.html
"""

import json
import os
import re
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", str(Path.home() / ".claude")))
MEMORY_DIR = CLAUDE_DIR / "projects"
INSIGHTS_FILE = CLAUDE_DIR / "learning" / "insights.jsonl"

# Keywords for scoring
USER_FACING = [
    "dashboard", "ui", "ux", "copilot", "skill", "mode", "mcp",
    "workflow", "deploy", "slack", "notification", "command",
]
INFRA = [
    "hook", "gate", "internal", "refactor", "ci", "test",
    "lint", "meta", "rsi", "budget",
]
SMALL_EFFORT = ["fix", "close", "wire", "add", "enable", "merge", "cleanup"]
LARGE_EFFORT = ["epic", "redesign", "architecture", "migration", "rewrite", "system", "engine"]

STATUS_URGENCY = {
    "BLOCKED": 20,
    "IN PR": 14,
    "IN_PROGRESS": 12,
    "IN PROGRESS": 12,
    "PENDING": 10,
    "IN ISSUE": 8,
    "LEARNED": 4,
    "DONE": 2,
    "FIXED": 2,
    "VERIFIED": 2,
    "BUILT": 2,
}

P0_THRESHOLD = 75
P1_THRESHOLD = 55
P2_THRESHOLD = 35


@dataclass
class ScoredItem:
    """A MEMORY.md TODO item with priority score breakdown."""
    id: int
    title: str
    origin: str = ""
    status: str = "PENDING"
    notes: str = ""
    impact: int = 0
    urgency: int = 0
    effort: int = 0
    dependencies: int = 0
    staleness: int = 0
    total_score: int = 0
    priority_label: str = "P3"
    column: str = "next-up"


def _find_memory_file() -> Optional[Path]:
    """Find the MEMORY.md file in the projects directory."""
    for p in MEMORY_DIR.rglob("MEMORY.md"):
        return p
    return None


def parse_memory_todos(memory_path: Optional[Path] = None) -> list:
    """Parse TODO table rows from MEMORY.md."""
    path = memory_path or _find_memory_file()
    if not path or not path.exists():
        return []

    text = path.read_text()

    # Only parse rows after the TODO table header to avoid matching
    # other pipe-delimited tables (e.g. Session Lineage).
    todo_start = text.find("## GRIP TODO Tracker")
    if todo_start == -1:
        todo_start = text.find("| # | Item |")  # fallback
    if todo_start == -1:
        return []
    text = text[todo_start:]

    items = []
    pattern = re.compile(
        r"^\|\s*(\d+)\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|\s*(.*?)\s*\|",
        re.MULTILINE,
    )
    for m in pattern.finditer(text):
        status_raw = m.group(4).strip()
        status = re.split(r"\s*\(", status_raw)[0].upper().strip()
        items.append({
            "id": int(m.group(1)),
            "title": m.group(2).strip(),
            "origin": m.group(3).strip(),
            "status": status,
            "notes": m.group(5).strip(),
        })
    return items


def parse_insights(insights_path: Optional[Path] = None) -> list:
    """Parse insights from JSONL file."""
    path = insights_path or INSIGHTS_FILE
    if not path.exists():
        return []
    items = []
    for line in path.read_text().strip().split("\n"):
        if not line.strip():
            continue
        try:
            items.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return items


def _score_impact(title: str, notes: str) -> int:
    """Score user-facing impact (0-20)."""
    text = (title + " " + notes).lower()
    score = 10
    if any(kw in text for kw in USER_FACING):
        score += 8
    if any(kw in text for kw in INFRA):
        score -= 4
    if "feature" in text or "user" in text:
        score += 4
    return max(0, min(20, score))


def _score_urgency(status: str) -> int:
    """Score urgency based on status (0-20)."""
    return STATUS_URGENCY.get(status, 10)


def _score_effort(title: str, notes: str) -> int:
    """Score effort inversely: quick wins score higher (0-20)."""
    text = (title + " " + notes).lower()
    score = 12
    if any(kw in text for kw in SMALL_EFFORT):
        score += 6
    if any(kw in text for kw in LARGE_EFFORT):
        score -= 6
    return max(0, min(20, score))


def _score_dependencies(notes: str) -> int:
    """Score based on dependency count (0-20). Fewer blockers = higher."""
    text = notes.lower()
    score = 14
    if "block" in text:
        score -= 8
    if "depends" in text or "requires" in text or "needs" in text:
        score -= 4
    if "standalone" in text or "independent" in text:
        score += 4
    return max(0, min(20, score))


def _score_staleness(origin: str) -> int:
    """Score staleness (0-20). Older origins get priority bump."""
    m = re.search(r"Gen\s*(\d+)", origin, re.IGNORECASE)
    if not m:
        return 10
    gen = int(m.group(1))
    age = max(0, 352 - gen)
    if age > 40:
        return 18
    if age > 20:
        return 14
    if age > 10:
        return 12
    return 8


def score_items(items: list) -> list:
    """Score parsed TODO items. Returns sorted by total_score descending."""
    scored = []
    for item in items:
        title = item.get("title", "")
        notes = item.get("notes", "")
        origin = item.get("origin", "")
        status = item.get("status", "PENDING")

        impact = _score_impact(title, notes)
        urgency = _score_urgency(status)
        effort = _score_effort(title, notes)
        deps = _score_dependencies(notes)
        stale = _score_staleness(origin)
        total = impact + urgency + effort + deps + stale

        if total >= P0_THRESHOLD:
            priority = "P0"
        elif total >= P1_THRESHOLD:
            priority = "P1"
        elif total >= P2_THRESHOLD:
            priority = "P2"
        else:
            priority = "P3"

        if status in ("DONE", "FIXED", "VERIFIED", "BUILT", "LEARNED"):
            column = "done"
        elif status in ("IN PR", "IN_PR"):
            column = "review"
        elif status in ("IN_PROGRESS", "IN PROGRESS"):
            column = "in-progress"
        elif status == "BLOCKED":
            column = "urgent"
        elif priority == "P0":
            column = "urgent"
        else:
            column = "next-up"

        scored.append(ScoredItem(
            id=item.get("id", 0),
            title=title, origin=origin, status=status, notes=notes,
            impact=impact, urgency=urgency, effort=effort,
            dependencies=deps, staleness=stale, total_score=total,
            priority_label=priority, column=column,
        ))

    scored.sort(key=lambda s: s.total_score, reverse=True)
    return scored


def items_to_kanban_data(scored: list) -> dict:
    """Convert scored items to grip_kanban input format."""
    cols = {
        "urgent": {"id": "urgent", "heading": "Urgent (P0)", "color": "#ef4444", "cards": []},
        "next-up": {"id": "next-up", "heading": "Next Up", "color": "#eab308", "cards": []},
        "in-progress": {"id": "in-progress", "heading": "In Progress", "color": "#3b82f6", "cards": []},
        "review": {"id": "review", "heading": "Review/PR", "color": "#a855f7", "cards": []},
        "done": {"id": "done", "heading": "Done", "color": "#22c55e", "cards": []},
    }
    for item in scored:
        priority_map = {"P0": "critical", "P1": "high", "P2": "medium", "P3": "low"}
        card = {
            "id": str(item.id),
            "title": f"#{item.id}: {item.title[:50]}",
            "description": f"Score: {item.total_score}/100 | {item.origin}",
            "priority": priority_map.get(item.priority_label, "low"),
            "tags": [item.status.lower(), item.priority_label],
        }
        target = cols.get(item.column, cols["next-up"])
        target["cards"].append(card)
    return {
        "title": "GRIP Backlog -- Prioritised Queue",
        "columns": [cols[k] for k in ("urgent", "next-up", "in-progress", "review", "done")],
    }


def items_to_data_table(scored: list) -> dict:
    """Convert scored items to grip_data_table input format."""
    return {
        "title": "GRIP Backlog -- Full Scored View",
        "columns": [
            {"id": "id", "label": "#", "sortable": True},
            {"id": "title", "label": "Item", "sortable": True},
            {"id": "status", "label": "Status", "sortable": True},
            {"id": "score", "label": "Score", "sortable": True},
            {"id": "priority", "label": "Priority", "sortable": True},
            {"id": "origin", "label": "Origin"},
            {"id": "breakdown", "label": "I/U/E/D/S"},
        ],
        "rows": [
            {
                "id": str(s.id),
                "title": s.title[:45],
                "status": s.status,
                "score": f"{s.total_score}/100",
                "priority": s.priority_label,
                "origin": s.origin,
                "breakdown": f"{s.impact}/{s.urgency}/{s.effort}/{s.dependencies}/{s.staleness}",
            }
            for s in scored
        ],
    }


def generate_html(scored_items: list, output_path: Path) -> None:
    """Regenerate the backlog-dashboard.html with fresh scored data."""
    template_path = output_path
    if not template_path.exists():
        print(f"Template not found: {template_path}", file=sys.stderr)
        sys.exit(1)
    html = template_path.read_text()
    json_data = json.dumps([asdict(s) for s in scored_items])
    html = re.sub(
        r'const DATA = \[.*?\];',
        f'const DATA = {json_data};',
        html,
        flags=re.DOTALL,
    )
    output_path.write_text(html)


def items_to_progress_data(scored: list) -> dict:
    """Convert scored items to grip_progress input format."""
    total = len(scored)
    done_n = sum(1 for s in scored if s.column == "done")
    in_prog = sum(1 for s in scored if s.column == "in-progress")
    in_rev = sum(1 for s in scored if s.column == "review")
    pending = total - done_n - in_prog - in_rev
    return {
        "title": "GRIP Backlog Completion",
        "steps": [
            {"id": "done", "label": f"Done ({done_n})", "status": "complete"},
            {"id": "review", "label": f"In Review ({in_rev})", "status": "active" if in_rev else "pending"},
            {"id": "progress", "label": f"In Progress ({in_prog})", "status": "active" if in_prog else "pending"},
            {"id": "pending", "label": f"Pending ({pending})", "status": "pending"},
        ],
        "percentage": round(done_n / total * 100) if total else 0,
    }


if __name__ == "__main__":
    items = parse_memory_todos()
    if not items:
        print("No TODO items found in MEMORY.md")
        sys.exit(1)
    scored = score_items(items)
    if "--json" in sys.argv:
        print(json.dumps([asdict(s) for s in scored], indent=2))
    elif "--kanban" in sys.argv:
        print(json.dumps(items_to_kanban_data(scored), indent=2))
    elif "--html" in sys.argv:
        output = Path(__file__).parent.parent / "backlog-dashboard.html"
        generate_html(scored, output)
        print(f"Dashboard regenerated: {output}")
    else:
        print(f"GRIP Backlog: {len(scored)} items scored\n")
        for s in scored[:20]:
            print(f"  [{s.priority_label}] #{s.id:>3d} ({s.total_score:>2d}/100) {s.title[:55]}")
        remaining = len(scored) - 20
        if remaining > 0:
            print(f"\n  ... and {remaining} more items")
