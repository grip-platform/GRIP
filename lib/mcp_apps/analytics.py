"""Analytics MCP app generators: session stats, health reports, contributors, decisions."""

import json
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

from .common import _run_git, _run_gh, CLAUDE_DIR, REPO_SLUG


def _ensure_own_lib() -> None:
    """Put the checkout's own lib folder on sys.path once, never the shared tree's (GRIP#9857)."""
    own_lib = str(Path(__file__).resolve().parent.parent)
    if own_lib not in sys.path:
        sys.path.insert(0, own_lib)


def generate_session_analytics() -> dict:
    """Generate grip_data_table with aggregate session statistics.

    Scans GRIP instance files and git log to build a table of recent sessions
    with commit count, duration estimate, and achievement info.
    """
    rows = []
    instances_dir = CLAUDE_DIR / "projects"

    # Scan for GRIP instance JSON files (UUID-named files in grip/ subdirs)
    instance_files = []
    for f in instances_dir.glob("**/grip/*.json"):
        # Skip non-instance files (branches.json, etc)
        if len(f.stem) > 30 and "-" in f.stem:
            instance_files.append(f)
    instance_files = sorted(instance_files, key=lambda p: p.stat().st_mtime, reverse=True)[:20]

    for inst_file in instance_files:
        try:
            with open(inst_file) as f:
                data = json.load(f)
            lineage = data.get("lineage", {})
            conv = data.get("conversation", {})
            session_id = data.get("instance_id", inst_file.stem)[:8]
            gen = lineage.get("lineage_depth", "?")
            msgs = conv.get("message_count", 0)
            branch = lineage.get("branch", "main")
            created = data.get("serialized_at", "")[:10]
            # Achievement from lineage chain (last entry)
            chain = lineage.get("lineage", [])
            achievement = (chain[-1].get("achievement") or "")[:40] if chain else ""
            rows.append({
                "session": session_id,
                "gen": str(gen),
                "messages": msgs,
                "branch": branch,
                "achievement": achievement,
                "date": created,
            })
        except (json.JSONDecodeError, OSError, KeyError):
            continue

    columns = [
        {"key": "session", "label": "Session", "width": "80px"},
        {"key": "gen", "label": "Gen", "width": "50px"},
        {"key": "messages", "label": "Msgs", "width": "60px", "sortable": True},
        {"key": "branch", "label": "Branch", "width": "120px"},
        {"key": "achievement", "label": "Achievement", "width": "250px"},
        {"key": "date", "label": "Date", "width": "100px", "sortable": True},
    ]

    return {
        "title": f"GRIP Session Analytics ({len(rows)} sessions)",
        "columns": columns,
        "rows": rows,
        "pageSize": 10,
        "searchable": True,
    }


def generate_health_report() -> dict:
    """Generate grip_digest with aggregate GRIP system health report.

    Collects metrics from git, GitHub issues, test suite, and delight snapshot
    to produce a periodic health digest with expandable detail cards.
    """
    cards = []

    # 1. Git velocity
    log_7d = _run_git(["log", "--since=7 days ago", "--oneline", "--no-merges"])
    commits_7d = len(log_7d.strip().split("\n")) if log_7d.strip() else 0
    log_24h = _run_git(["log", "--since=24 hours ago", "--oneline", "--no-merges"])
    commits_24h = len(log_24h.strip().split("\n")) if log_24h.strip() else 0
    velocity_status = "success" if commits_24h >= 5 else "warning" if commits_24h >= 1 else "error"
    cards.append({
        "id": "velocity",
        "title": "Commit Velocity",
        "icon": "V",
        "summary": f"{commits_24h} commits today, {commits_7d} this week",
        "status": velocity_status,
        "detail": log_24h[:500] if log_24h else "No commits in last 24h",
    })

    # 2. Issue health
    issues_json = _run_gh(["issue", "list", "-R", REPO_SLUG, "--state", "open", "--json", "number,labels", "--limit", "100"])
    open_count, sprint_count, bug_count = 0, 0, 0
    if issues_json:
        try:
            issues = json.loads(issues_json)
            open_count = len(issues)
            sprint_count = sum(1 for i in issues if any(l.get("name") == "sprint-loop" for l in i.get("labels", [])))
            bug_count = sum(1 for i in issues if any(l.get("name") == "bug" for l in i.get("labels", [])))
        except json.JSONDecodeError:
            pass
    issue_status = "success" if bug_count == 0 else "warning" if bug_count < 3 else "error"
    cards.append({
        "id": "issues",
        "title": "Issue Tracker",
        "icon": "I",
        "summary": f"{open_count} open ({sprint_count} sprint-loop, {bug_count} bugs)",
        "status": issue_status,
    })

    # 3. Test suite
    test_result = ""
    try:
        result = subprocess.run(
            ["python3", "-m", "pytest", "tests/", "--tb=no", "-q", "--no-header"],
            capture_output=True, text=True, timeout=30, cwd=str(CLAUDE_DIR),
        )
        test_result = result.stdout.strip()
        passed = "passed" in test_result and "failed" not in test_result
    except (subprocess.TimeoutExpired, FileNotFoundError):
        passed = False
        test_result = "Test run timed out or unavailable"
    test_status = "success" if passed else "error"
    cards.append({
        "id": "tests",
        "title": "Test Suite",
        "icon": "T",
        "summary": test_result.split("\n")[-1] if test_result else "No test output",
        "status": test_status,
        "detail": test_result[:500],
    })

    # 4. Delight snapshot
    delight_summary = "Unavailable"
    delight_status = "info"
    try:
        _ensure_own_lib()
        from auto_converge import delight_snapshot
        snap = delight_snapshot()
        composite = snap.get("composite", 0)
        delight_summary = f"Composite: {composite:.3f}"
        delight_status = "success" if composite > 0.5 else "warning" if composite > 0.2 else "error"
    except Exception:
        pass
    cards.append({
        "id": "delight",
        "title": "Delight Score",
        "icon": "D",
        "summary": delight_summary,
        "status": delight_status,
    })

    return {"title": "GRIP Health Report", "cards": cards}


def generate_progress_tracker(milestones: list, current_progress: int = None) -> dict:
    """Generate grip_progress data for sprint/project tracking.

    Args:
        milestones: List of milestone dicts with keys: label, reached (bool)
        current_progress: Percentage complete (0-100), computed if None

    Returns:
        Dict with structure: {title, percentage, milestones, detail}
    """
    if current_progress is None:
        reached_count = sum(1 for m in milestones if m.get("reached", False))
        current_progress = int((reached_count / max(len(milestones), 1)) * 100)

    return {
        "title": "Sprint Progress",
        "percentage": min(current_progress, 100),
        "milestones": milestones,
        "detail": f"{current_progress}% complete"
    }


def generate_contributor_data_table(contributor_data: dict = None) -> dict:
    """Generate grip_data_table from contributor_insights output.

    Args:
        contributor_data: Dict with contributors list. If None, analyzes git log.

    Returns:
        Dict for grip_data_table: {title, columns, rows, searchable, pageSize}
    """
    if contributor_data is None:
        try:
            from contributor_insights import analyze_contributors, parse_git_log
            log_text = _run_git(["log", "--format=%H|%an|%ae|%aI|%s", "--numstat", "-200"])
            if log_text:
                commits = parse_git_log(log_text.splitlines())
                contribs = analyze_contributors(commits)
                contributor_data = {
                    "contributors": [
                        {
                            "name": c.name,
                            "commits": c.total_commits,
                            "additions": c.total_additions,
                            "deletions": c.total_deletions,
                            "files": c.files_touched,
                            "first": c.first_commit[:10] if c.first_commit else "",
                            "last": c.last_commit[:10] if c.last_commit else "",
                            "streak": c.current_streak,
                            "top_areas": ", ".join(c.top_areas[:3]),
                        }
                        for c in contribs
                    ]
                }
            else:
                contributor_data = {"contributors": []}
        except (ImportError, Exception):
            contributor_data = {"contributors": []}

    columns = [
        {"key": "name", "label": "Contributor", "sortable": True, "width": "20%"},
        {"key": "commits", "label": "Commits", "sortable": True, "width": "10%"},
        {"key": "additions", "label": "Additions", "sortable": True, "width": "10%"},
        {"key": "deletions", "label": "Deletions", "sortable": True, "width": "10%"},
        {"key": "files", "label": "Files", "sortable": True, "width": "10%"},
        {"key": "streak", "label": "Streak", "sortable": True, "width": "8%"},
        {"key": "top_areas", "label": "Top Areas", "sortable": False, "width": "22%"},
        {"key": "last", "label": "Last Active", "sortable": True, "width": "10%"},
    ]

    rows = contributor_data.get("contributors", [])

    return {
        "title": "Contributor Insights",
        "columns": columns,
        "rows": rows,
        "searchable": True,
        "pageSize": 10,
    }


def generate_decision_health_circuit() -> dict:
    """Generate circuit dashboard showing decision outcome health.

    Visualizes decision resolution rates by ritual, highlighting
    unresolved decisions and ghost entries (data quality issues).
    Powered by outcome_discovery.py.
    """
    try:
        from aide.outcome_discovery import resolution_stats
        from aide.decision_logger import DecisionLogger
    except ImportError:
        return {"title": "Decision Health", "circuits": [], "summary": "Module not available"}

    stats = resolution_stats()
    dl = DecisionLogger()

    total = stats.get("decisions", 0)
    outcomes = stats.get("outcomes", 0)
    resolution_rate = stats.get("resolution_rate", 0)
    by_ritual = stats.get("by_ritual", {})

    circuits = []

    # Overall health indicator
    if resolution_rate >= 0.7:
        overall_status = "green"
    elif resolution_rate >= 0.4:
        overall_status = "yellow"
    else:
        overall_status = "red"

    circuits.append({
        "name": "Overall Resolution",
        "status": overall_status,
        "value": f"{resolution_rate:.0%}",
        "label": f"{outcomes}/{total} resolved",
    })

    # Per-ritual breakdown
    for ritual_id, count in sorted(by_ritual.items(), key=lambda x: -x[1]):
        display_name = ritual_id if ritual_id else "(no ritual)"
        if count == 0:
            status = "green"
            label = "All resolved"
        elif count <= 3:
            status = "yellow"
            label = f"{count} unresolved"
        else:
            status = "red"
            label = f"{count} unresolved"

        circuits.append({
            "name": display_name[:25],
            "status": status,
            "value": str(count),
            "label": label,
        })

    # Ghost decision detection
    unresolved = dl.get_unresolved_decisions(max_age_hours=168)
    ghosts = sum(
        1 for d in unresolved
        if d.get("decision", {}).get("what", "") in ("", "Decision")
    )
    if ghosts > 0:
        circuits.append({
            "name": "Ghost Decisions",
            "status": "red",
            "value": str(ghosts),
            "label": "Empty what/step_id -- data quality issue",
        })

    return {
        "title": f"Decision Health ({resolution_rate:.0%} resolved)",
        "description": "Decision outcome tracking and resolution health",
        "circuits": circuits,
        "summary": {
            "total_decisions": total,
            "total_outcomes": outcomes,
            "resolution_rate": round(resolution_rate, 3),
            "unresolved_7d": stats.get("unresolved_7d", 0),
            "ghost_decisions": ghosts,
            "rituals_tracked": len(by_ritual),
        },
    }
