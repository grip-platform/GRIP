#!/usr/bin/env python3
"""
Holistic Backlog Reader — aggregates pending work from every GRIP source.

Sources (in priority-signal order):
  1. Deferred AGORA follow-ups (oldest-first, expiry-ranked — see below)
  2. GitHub open issues  (gh issue list)
  3. Instance next_up    (projects/*/grip/index.json)
  4. plans/*.md          (unchecked checklist items)
  5. state/queue/pending/*.md  (filesystem mtime)
  6. MEMORY.md TODO markers
  7. Hypothesis deadlines (lib/hypothesis_engine.py status)

Output: deduplicated list of BacklogItem, sorted by
  priority × (1 / effort) × deadline_proximity.

Origin: heartbeat-backlog-consumer plan (2026-04-27, Gen 511+).
Primary Author: V>>
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from itertools import chain
from pathlib import Path
from typing import Optional

CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", Path.home() / ".claude"))

# Priority constants — higher = more urgent.
PRIORITY_P0 = 100
PRIORITY_P1 = 75
PRIORITY_SPRINT = 60
PRIORITY_SECURITY = 90
PRIORITY_DEFAULT = 40
PRIORITY_DEADLINE_OVERDUE = 95
PRIORITY_DEADLINE_DUE = 70

# Labels that map to priority signals.
_LABEL_PRIORITY: dict[str, int] = {
    "P0": PRIORITY_P0,
    "P1": PRIORITY_P1,
    "security": PRIORITY_SECURITY,
    "sprint-loop": PRIORITY_SPRINT,
}

# --- Deferred AGORA follow-ups -------------------------------------------
# AGORA files a DEFER_CHANGES follow-up under this label
# (lib/agora_pr_review.py::_DEFER_ISSUE_LABELS). Without the reader below they
# accumulate forever: _read_github_issues caps at _GH_ISSUE_LIMIT NEWEST issues,
# so once the open-issue count passes that cap the oldest deferrals fall out of
# the only window anything reads. Measured 2026-08-10: 144 open issues, 71
# labelled sprint-loop, oldest reachable #4383 (2026-08-05) — the oldest
# deferral (#2808, 2026-05-17) was structurally invisible. GH #4467.
DEFERRED_LABEL = "sprint-loop"
DEFERRED_OLDEST_N = 10        # per pulse: route the oldest N back, not a full sync
DEFERRED_EXPIRY_DAYS = 30     # past this age a deferral is overdue, not pending

_GH_ISSUE_LIMIT = 50          # newest-first window used by _read_github_issues


@dataclass
class BacklogItem:
    """A single actionable pending work item."""

    id: str                          # Unique within this read cycle
    title: str
    source: str                      # e.g. "github", "next_up", "plan", "queue", "memory", "hypothesis"
    priority: int                    # 0-100, higher = more urgent
    est_effort: int                  # Rough token estimate (1=trivial … 5=large)
    complexity_tier: str             # "FAST" | "CAREFUL" | "VERIFY"
    deadline: Optional[datetime] = field(default=None)
    meta: dict = field(default_factory=dict)

    @property
    def score(self) -> float:
        """Composite sort key: priority × (1/effort) × deadline boost."""
        base = self.priority / max(self.est_effort, 1)
        if self.deadline:
            now = datetime.now(timezone.utc)
            days_left = (self.deadline - now).total_seconds() / 86400
            if days_left <= 0:
                base *= 2.0   # overdue — double urgency
            elif days_left <= 7:
                base *= 1.5
        return base


# ---------------------------------------------------------------------------
# Source readers
# ---------------------------------------------------------------------------

def _gh_issue_list(extra_args: list[str], limit: int) -> list[dict]:
    """One fail-safe ``gh issue list``; returns parsed rows, ``[]`` on any failure.

    ``cwd`` pins repo resolution to the GRIP checkout — ``gh`` otherwise resolves
    from the caller's cwd and returns an EMPTY list with exit 0 on the wrong repo
    (rules/gh-repo-resolution.md), which is indistinguishable from "no issues".
    """
    argv = ["gh", "issue", "list", "--state", "open",
            "--json", "number,title,labels,createdAt",
            "--limit", str(limit)] + extra_args
    try:
        result = subprocess.run(
            argv, capture_output=True, text=True, timeout=15, cwd=str(CLAUDE_DIR),
        )
        if result.returncode != 0:
            return []
        rows = json.loads(result.stdout or "[]")
    except Exception:
        return []
    return rows if isinstance(rows, list) else []


def _issue_to_item(issue: dict, deadline: Optional[datetime] = None) -> BacklogItem:
    """Map one ``gh issue list`` row onto a BacklogItem. Shared by both GitHub readers."""
    label_names = [lb.get("name", "") for lb in issue.get("labels", [])]
    priority = max(
        (_LABEL_PRIORITY.get(ln, 0) for ln in label_names),
        default=PRIORITY_DEFAULT,
    )
    return BacklogItem(
        id=f"gh-{issue['number']}",
        title=issue["title"],
        source="github",
        priority=priority,
        est_effort=3,
        complexity_tier="CAREFUL",
        deadline=deadline,
        meta={"labels": label_names},
    )


def _parse_created_at(raw: str) -> Optional[datetime]:
    """Parse a gh ``createdAt`` timestamp. Returns None on anything unparseable."""
    try:
        return datetime.fromisoformat((raw or "").replace("Z", "+00:00"))
    except ValueError:
        return None


def _read_github_issues() -> list[BacklogItem]:
    """Read the newest open GitHub issues from the GRIP repo.

    Capped at ``_GH_ISSUE_LIMIT`` NEWEST issues, so anything older than that
    window is invisible here — that is what ``_read_deferred_followups`` exists
    to cover.
    """
    return [_issue_to_item(i) for i in _gh_issue_list([], _GH_ISSUE_LIMIT)]


def _read_deferred_followups() -> list[BacklogItem]:
    """Route the OLDEST deferred AGORA follow-ups back into the backlog.

    Two things are needed to break the accumulation, and only doing the first is
    a no-op:

    1. **Reach them.** Fetch oldest-first and label-scoped, so a deferral that
       has aged out of the newest-issue window is still read.
    2. **Rank them.** Every ``sprint-loop`` issue scores identically
       (``PRIORITY_SPRINT / est_effort`` = 20.0), and ``read_top_k`` sorts with
       Python's STABLE sort — so merely appending old items leaves them behind
       every fresher tie and they would still never reach the top-k. Each one
       therefore carries a deadline of ``created + DEFERRED_EXPIRY_DAYS``, which
       ``BacklogItem.score`` already turns into a 1.5x boost as it nears expiry
       and 2.0x once overdue. An aged deferral (40.0) then genuinely outranks a
       fresh one (20.0).

    That deadline is also the missing lifecycle bound: per
    ``rules/lifecycle-closed-or-expired.md`` an unclosed record with no deadline
    is exactly what a deferred concern was. GH #4467.
    """
    # On the two review concerns raised against this call, both tested rather
    # than argued, both refuted (2026-08-10):
    #
    # 1. "`--search` replaces `--label`/`--state`, so closed or unlabelled
    #    issues leak in." Measured against the live repo with the exact flag
    #    set below: label+state alone returned 71, label+state+search returned
    #    71, closed issues leaking through = 0, non-sprint-loop issues leaking
    #    through = 0. The filters compose; they are not overridden.
    #
    # 2. "A static DEFERRED_OLDEST_N starves the oldest items when inflow
    #    outruns processing." Inverted: the sort is created-ASC, so the oldest
    #    are precisely the ones taken every run. Items age INTO the window, not
    #    out of it. Starvation would need newest-first.
    #
    # Recorded here so the next reader does not re-raise either and "fix" a
    # non-bug. If a future gh release does change the composition rule, the
    # first concern becomes real — re-run the count comparison above to check.
    rows = _gh_issue_list(
        ["--label", DEFERRED_LABEL, "--search", "sort:created-asc"],
        DEFERRED_OLDEST_N,
    )
    expiry = timedelta(days=DEFERRED_EXPIRY_DAYS)
    items = []
    for issue in rows:
        created = _parse_created_at(issue.get("createdAt", ""))
        items.append(_issue_to_item(issue, deadline=created + expiry if created else None))
    return items


def _read_next_up() -> list[BacklogItem]:
    """Read next_up fields from project instance JSON files."""
    items = []
    projects_dir = CLAUDE_DIR / "projects"
    if not projects_dir.exists():
        return items

    for index_path in projects_dir.glob("*/grip/index.json"):
        try:
            data = json.loads(index_path.read_text())
            next_up = data.get("next_up", "")
            if not next_up or not next_up.strip():
                continue
            proj = index_path.parent.parent.name
            items.append(BacklogItem(
                id=f"next_up-{proj}",
                title=next_up.strip()[:120],
                source="next_up",
                priority=PRIORITY_DEFAULT,
                est_effort=2,
                complexity_tier="FAST",
                meta={"project": proj},
            ))
        except Exception:
            continue
    return items


def _read_plan_todos() -> list[BacklogItem]:
    """Parse unchecked checklist items from non-gitignored plans/*.md."""
    items = []
    plans_dir = CLAUDE_DIR / "plans"
    if not plans_dir.exists():
        return items

    checkbox_re = re.compile(r"^\s*-\s+\[ \]\s+(.+)$")

    def _items_for_plan(plan_file):
        try:
            text = plan_file.read_text(errors="replace")
        except Exception:
            return []
        return [
            BacklogItem(
                id=f"plan-{plan_file.stem}-{hash(m.group(1).strip()) & 0xFFFF:04x}",
                title=m.group(1).strip()[:120],
                source="plan",
                priority=PRIORITY_DEFAULT,
                est_effort=3,
                complexity_tier="FAST",
                meta={"file": plan_file.name},
            )
            for m in checkbox_re.finditer(text)
        ]

    items = list(chain.from_iterable(
        _items_for_plan(p) for p in sorted(plans_dir.glob("*.md"))
    ))
    return items


def _read_queue_pending() -> list[BacklogItem]:
    """List task files from state/queue/pending/, ordered by mtime."""
    queue_dir = CLAUDE_DIR / "state" / "queue" / "pending"
    if not queue_dir.exists():
        return []

    items = []
    for task_file in sorted(queue_dir.glob("*.md"), key=lambda p: p.stat().st_mtime):
        title = task_file.stem.replace("-", " ").replace("_", " ")
        items.append(BacklogItem(
            id=f"queue-{task_file.stem}",
            title=title[:120],
            source="queue",
            priority=PRIORITY_DEFAULT,
            est_effort=2,
            complexity_tier="FAST",
            meta={"path": str(task_file)},
        ))
    return items


def _read_memory_todos() -> list[BacklogItem]:
    """Grep MEMORY.md files for TODO: markers."""
    todo_re = re.compile(r"TODO:\s*(.+)", re.IGNORECASE)

    memory_files = list(CLAUDE_DIR.glob("projects/*/memory/MEMORY.md"))
    memory_files += list(CLAUDE_DIR.glob("memory/MEMORY.md"))

    def _items_for_memory(mem_file):
        try:
            text = mem_file.read_text(errors="replace")
        except Exception:
            return []
        return [
            BacklogItem(
                id=f"memory-{hash(m.group(1).strip()) & 0xFFFF:04x}",
                title=m.group(1).strip()[:120],
                source="memory",
                priority=PRIORITY_DEFAULT,
                est_effort=2,
                complexity_tier="FAST",
                meta={"file": str(mem_file)},
            )
            for m in todo_re.finditer(text)
        ]

    return list(chain.from_iterable(
        _items_for_memory(f) for f in memory_files
    ))


def _read_hypothesis_deadlines() -> list[BacklogItem]:
    """Surface due/overdue hypotheses from hypothesis_engine."""
    try:
        # run_text, not subprocess.run(text=True): the hypothesis IDs pulled out
        # of this output become backlog item text the operator reads.
        from _compat import run_text
        result = run_text(
            [str(CLAUDE_DIR / "venv" / "bin" / "python"),
             str(CLAUDE_DIR / "lib" / "hypothesis_engine.py"), "status"],
            capture_output=True, timeout=20,
            cwd=str(CLAUDE_DIR),
        )
        output = result.stdout
    except Exception:
        return []

    due_re = re.compile(r"Due for verification:\s*\[([^\]]*)\]")
    match = due_re.search(output)
    if not match:
        return []

    due_ids = [h.strip().strip("'\"") for h in match.group(1).split(",") if h.strip()]
    items = []
    for hyp_id in due_ids:
        if not hyp_id:
            continue
        items.append(BacklogItem(
            id=f"hyp-{hyp_id}",
            title=f"Verify hypothesis {hyp_id}",
            source="hypothesis",
            priority=PRIORITY_DEADLINE_DUE,
            est_effort=3,
            complexity_tier="VERIFY",
            deadline=datetime.now(timezone.utc),  # treat as due-now
            meta={"hypothesis_id": hyp_id},
        ))
    return items


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------

def _dedup(items: list[BacklogItem]) -> list[BacklogItem]:
    """Remove items with duplicate ids; first occurrence wins."""
    seen: set[str] = set()
    result = []
    for item in items:
        if item.id not in seen:
            seen.add(item.id)
            result.append(item)
    return result


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def read_top_k(k: int = 1) -> list[BacklogItem]:
    """Aggregate all sources, dedup, sort by score, return top k items.

    Args:
        k: Number of items to return. Default 1 (safe for heartbeat dispatch).

    Returns:
        Up to k BacklogItem instances, highest-score first.
    """
    all_items: list[BacklogItem] = []
    for reader in (
        # Deferred follow-ups FIRST: _dedup is first-occurrence-wins, and an
        # issue in BOTH windows must keep the deadline-bearing copy. Reversed,
        # a deferral would silently lose its expiry boost the moment the open
        # count fell back under _GH_ISSUE_LIMIT and both readers saw it.
        _read_deferred_followups,
        _read_github_issues,
        _read_next_up,
        _read_plan_todos,
        _read_queue_pending,
        _read_memory_todos,
        _read_hypothesis_deadlines,
    ):
        all_items.extend(reader())

    deduped = _dedup(all_items)
    deduped.sort(key=lambda it: it.score, reverse=True)
    return deduped[:k]


if __name__ == "__main__":
    import sys
    k = int(sys.argv[1]) if len(sys.argv) > 1 else 5
    results = read_top_k(k)
    if not results:
        print("No backlog items found.")
        sys.exit(0)
    for item in results:
        print(f"[{item.source:10s}] pri={item.priority:3d} eff={item.est_effort} "
              f"tier={item.complexity_tier:8s} score={item.score:6.1f}  {item.title}")
