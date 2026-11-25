#!/usr/bin/env python3
"""Session Intelligence Report — end-of-session synthesis.

Combines data from 6+ sources into a single actionable report:
  1. Git activity (commits, files changed, branches)
  2. Decision health (resolution rate, unresolved, ghost decisions)
  3. Delight metrics (composite, trend)
  4. Convergence performance (if converge() ran)
  5. Open issues / PR status
  6. Session duration and context usage

Output: structured JSON report + human-readable markdown summary.

Usage:
    python3 lib/session_intelligence.py report          # Full report (JSON)
    python3 lib/session_intelligence.py summary         # Human-readable summary
    python3 lib/session_intelligence.py recommendations # Next-session focus items
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from lib.subprocess_utils import run_git, run_gh

CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", str(Path.home() / ".claude")))


def _git_activity(hours: float = 8) -> Dict[str, Any]:
    """Gather git activity from recent history."""
    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()

    # Recent commits
    raw = run_git(["log", f"--since={since}", "--format=%H|%ai|%s", "--no-merges"], timeout=10)
    commits = []
    for line in (raw or "").strip().split("\n"):
        if not line:
            continue
        parts = line.split("|", 2)
        if len(parts) >= 3:
            commits.append({"hash": parts[0][:8], "date": parts[1], "message": parts[2]})

    # Files changed
    files_raw = run_git(["diff", "--stat", f"HEAD~{min(len(commits), 20)}", "HEAD"], timeout=10) if commits else ""
    files_changed = len([l for l in (files_raw or "").split("\n") if l.strip() and "|" in l])

    # Current branch
    branch = run_git(["rev-parse", "--abbrev-ref", "HEAD"], timeout=5)

    # Uncommitted changes
    status = run_git(["status", "--porcelain"], timeout=5)
    uncommitted = len([l for l in (status or "").split("\n") if l.strip()])

    return {
        "commits": len(commits),
        "recent_commits": commits[:10],
        "files_changed": files_changed,
        "branch": branch or "unknown",
        "uncommitted_changes": uncommitted,
        "hours_covered": hours,
    }


def _decision_health() -> Dict[str, Any]:
    """Get current decision health metrics."""
    try:
        from lib.aide.outcome_discovery import resolution_stats
        return resolution_stats()
    except Exception:
        return {"error": "outcome_discovery not available"}


def _delight_metrics() -> Dict[str, Any]:
    """Get current delight metrics."""
    try:
        from lib.auto_converge import delight_trend
        return delight_trend(days=7)
    except Exception:
        return {"error": "delight_trend not available"}


def _convergence_recent() -> Dict[str, Any]:
    """Get most recent convergence run metrics."""
    metrics_file = CLAUDE_DIR / "learning" / "converge_metrics.jsonl"
    if not metrics_file.exists():
        return {"runs": 0}

    try:
        lines = metrics_file.read_text().strip().split("\n")
        entries = [json.loads(l) for l in lines[-20:] if l.strip()]
        end_entries = [e for e in entries if e.get("event") == "end"]
        if not end_entries:
            return {"runs": 0}

        latest = end_entries[-1]
        return {
            "runs": len(end_entries),
            "latest_run_id": latest.get("run_id", ""),
            "latest_satisfied": latest.get("satisfied", False),
            "latest_depth": latest.get("depth_reached", 0),
            "latest_velocity": latest.get("velocity_trajectory", []),
            "latest_elapsed_min": latest.get("elapsed_minutes", 0),
        }
    except Exception:
        return {"runs": 0, "error": "parse failure"}


def _open_prs() -> List[Dict]:
    """Get open PRs from GitHub."""
    raw = run_gh(["pr", "list", "--state", "open", "--json", "number,title,author,createdAt"], timeout=15)
    if not raw:
        return []
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []


def _genome_state() -> Dict[str, Any]:
    """Get current genome generation and fitness."""
    genome_file = CLAUDE_DIR / "data" / "genome.json"
    if not genome_file.exists():
        return {"generation": 0}
    try:
        data = json.loads(genome_file.read_text())
        return {
            "generation": data.get("generation", 0),
            "id": data.get("id", "")[:8],
            "gene_count": len(data.get("genes", [])),
            "fitness": data.get("fitness", 0),
        }
    except Exception:
        return {"generation": 0, "error": "parse failure"}


def generate_report(hours: float = 8) -> Dict[str, Any]:
    """Generate comprehensive session intelligence report."""
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "git": _git_activity(hours),
        "decisions": _decision_health(),
        "delight": _delight_metrics(),
        "convergence": _convergence_recent(),
        "open_prs": _open_prs(),
        "genome": _genome_state(),
    }

    # Compute session health score (0-100)
    scores = []
    # Git activity: commits = productivity
    git = report["git"]
    if git["commits"] > 0:
        scores.append(min(100, git["commits"] * 12))  # 8+ commits = 100
    else:
        scores.append(20)

    # Decision resolution rate
    decisions = report["decisions"]
    if "resolution_rate" in decisions:
        scores.append(decisions["resolution_rate"] * 100)
    else:
        scores.append(50)

    # Delight composite
    delight = report["delight"]
    if "composite_avg" in delight:
        scores.append(delight["composite_avg"] * 100)
    else:
        scores.append(50)

    report["health_score"] = round(sum(scores) / max(len(scores), 1), 1)

    return report


def generate_summary(hours: float = 8) -> str:
    """Generate human-readable session summary."""
    r = generate_report(hours)
    lines = []

    lines.append(f"# Session Intelligence Report")
    lines.append(f"Generated: {r['generated_at'][:19]}Z | Health: {r['health_score']}/100")
    lines.append("")

    # Git activity
    git = r["git"]
    lines.append(f"## Git Activity ({git['hours_covered']}h window)")
    lines.append(f"- Branch: {git['branch']}")
    lines.append(f"- Commits: {git['commits']} | Files changed: {git['files_changed']}")
    if git["uncommitted_changes"] > 0:
        lines.append(f"- Uncommitted changes: {git['uncommitted_changes']}")
    if git["recent_commits"]:
        lines.append("- Recent:")
        for c in git["recent_commits"][:5]:
            lines.append(f"  - {c['hash']} {c['message'][:60]}")
    lines.append("")

    # Decisions
    d = r["decisions"]
    if "resolution_rate" in d:
        rate = d["resolution_rate"]
        lines.append(f"## Decision Health ({rate:.0%} resolved)")
        lines.append(f"- Total: {d.get('decisions', 0)} decisions, {d.get('outcomes', 0)} outcomes")
        lines.append(f"- Unresolved (7d): {d.get('unresolved_7d', 0)}")
        by_ritual = d.get("by_ritual", {})
        if by_ritual:
            top = sorted(by_ritual.items(), key=lambda x: -x[1])[:3]
            lines.append(f"- Top unresolved: {', '.join(f'{k}({v})' for k, v in top)}")
    lines.append("")

    # Delight
    dl = r["delight"]
    if "composite_avg" in dl:
        lines.append(f"## Delight Metrics")
        lines.append(f"- Composite: {dl['composite_avg']:.3f} | Trend: {dl.get('trend', '?')}")
        lines.append(f"- User delight: {dl.get('user_delight_avg', 0):.3f} | System health: {dl.get('system_health_avg', 0):.3f}")
    lines.append("")

    # Convergence
    cv = r["convergence"]
    if cv.get("runs", 0) > 0:
        lines.append(f"## Convergence")
        lines.append(f"- Total runs: {cv['runs']} | Latest depth: {cv.get('latest_depth', 0)}")
        lines.append(f"- Latest satisfied: {cv.get('latest_satisfied', False)}")
    lines.append("")

    # Open PRs
    prs = r["open_prs"]
    if prs:
        lines.append(f"## Open PRs ({len(prs)})")
        for pr in prs[:5]:
            lines.append(f"- #{pr['number']} {pr['title'][:50]}")
    lines.append("")

    # Genome
    g = r["genome"]
    if g.get("generation", 0) > 0:
        lines.append(f"## Genome: Gen {g['generation']} ({g.get('gene_count', 0)} genes)")
    lines.append("")

    return "\n".join(lines)


def generate_recommendations(hours: float = 8) -> List[str]:
    """Generate next-session focus recommendations."""
    r = generate_report(hours)
    recs = []

    # Decision-based recommendations
    d = r["decisions"]
    unresolved = d.get("unresolved_7d", 0)
    if unresolved > 10:
        recs.append(f"Resolve {unresolved} unresolved decisions (run: python3 lib/copilot/outcome_discovery.py discover)")

    by_ritual = d.get("by_ritual", {})
    if by_ritual.get("rsi-autonomous", 0) > 5:
        recs.append("Fix rsi-autonomous decision logging — ghost entries accumulating")

    # Git-based recommendations
    git = r["git"]
    if git["uncommitted_changes"] > 0:
        recs.append(f"Commit or stash {git['uncommitted_changes']} uncommitted changes")

    # PR-based recommendations
    prs = r["open_prs"]
    old_prs = [pr for pr in prs if pr.get("createdAt", "") < (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()]
    if old_prs:
        nums = ", ".join(f"#{pr['number']}" for pr in old_prs[:3])
        recs.append(f"Review aging PRs: {nums} (>3 days old)")

    # Delight-based recommendations
    dl = r["delight"]
    if dl.get("system_health_avg", 1) < 0.3:
        recs.append("System health is low — check KONO substrate stats and genome fitness")
    if dl.get("trend") == "declining":
        recs.append("Delight trend is declining — review recent changes for regressions")

    # Convergence-based recommendations
    cv = r["convergence"]
    if cv.get("latest_satisfied") is False and cv.get("runs", 0) > 0:
        recs.append("Last convergence run was unsatisfied — investigate blockers")

    if not recs:
        recs.append("All clear — continue with current priorities")

    return recs


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "summary"
    hours = float(sys.argv[2]) if len(sys.argv) > 2 else 8

    if cmd == "report":
        print(json.dumps(generate_report(hours), indent=2, default=str))
    elif cmd == "summary":
        print(generate_summary(hours))
    elif cmd == "recommendations":
        recs = generate_recommendations(hours)
        for i, r in enumerate(recs, 1):
            print(f"{i}. {r}")
    else:
        print(f"Usage: {sys.argv[0]} [report|summary|recommendations] [hours]")
        sys.exit(1)


if __name__ == "__main__":
    main()
