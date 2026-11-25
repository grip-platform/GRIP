"""Sprint-related MCP app generators: kanban, timeline, progress."""

import json
from .common import _run_git, _run_gh, REPO_SLUG


def generate_sprint_kanban(issues: list = None) -> dict:
    """Generate grip_kanban data from GitHub issues.

    Args:
        issues: List of GitHub issue dicts (from gh issue list --json).
                If None, fetches from GitHub.

    Returns:
        Dict with structure: {title, columns: [{id, heading, color, cards}]}
    """
    if issues is None:
        output = _run_gh([
            "issue", "list", "-R", REPO_SLUG, "--state", "open",
            "--json", "number,title,labels,createdAt,assignees",
            "--limit", "50"
        ])
        if not output:
            issues = []
        else:
            try:
                issues = json.loads(output)
            except json.JSONDecodeError:
                issues = []

    # Classify issues into columns
    backlog_cards = []
    in_progress_cards = []
    review_cards = []
    done_cards = []

    for issue in issues:
        labels = [l.get("name", "") for l in issue.get("labels", [])]
        number = issue.get("number", 0)
        title = issue.get("title", "")
        assignees = issue.get("assignees", [])

        # Determine priority from labels
        priority = "medium"
        if any(l in ("P0", "bug") for l in labels):
            priority = "high"
        elif any(l in ("P3", "nice-to-have") for l in labels):
            priority = "low"

        # Build card
        card = {
            "id": str(number),
            "title": f"#{number}: {title[:50]}",
            "description": f"Assignees: {len(assignees)}" if assignees else "",
            "priority": priority,
            "tags": [l for l in labels if l.startswith("P") or l in ("bug", "feature", "enhancement")][:2],
        }

        # Classify by labels
        if "in-progress" in labels or "sprint-loop" in labels:
            in_progress_cards.append(card)
        elif "review" in labels or "pr-open" in labels:
            review_cards.append(card)
        elif "done" in labels or "completed" in labels:
            done_cards.append(card)
        else:
            backlog_cards.append(card)

    return {
        "title": "Sprint Board",
        "columns": [
            {"id": "backlog", "heading": "Backlog", "color": "#3b82f6", "cards": backlog_cards[:12]},
            {"id": "in-progress", "heading": "In Progress", "color": "#eab308", "cards": in_progress_cards[:8]},
            {"id": "review", "heading": "Review", "color": "#f97316", "cards": review_cards[:6]},
            {"id": "done", "heading": "Done", "color": "#22c55e", "cards": done_cards[:4]},
        ]
    }


def generate_sprint_timeline(commits: list = None, prs: list = None) -> dict:
    """Generate grip_timeline from git commits and PRs.

    Args:
        commits: List of commit dicts with keys: sha, message, author, date
        prs: List of PR dicts with keys: number, title, mergedAt

    Returns:
        Dict with structure: {title, events: [{date, label, description, category}]}
    """
    events = []

    # Process commits
    if commits is None:
        log_output = _run_git(["log", "--since=7 days ago", "--format=%H|%s|%an|%aI", "--no-merges"])
        if log_output:
            for line in log_output.strip().split("\n"):
                if not line:
                    continue
                parts = line.split("|", 3)
                if len(parts) == 4:
                    sha, msg, author, date = parts
                    events.append({
                        "date": date,
                        "label": msg[:50],
                        "description": f"by {author}",
                        "category": "commit"
                    })
    else:
        for c in commits:
            events.append({
                "date": c.get("date", ""),
                "label": c.get("message", "")[:50],
                "description": f"by {c.get('author', 'unknown')}",
                "category": "commit"
            })

    # Process PRs
    if prs is None:
        merged_json = _run_gh([
            "pr", "list", "-R", REPO_SLUG, "--state", "merged",
            "--json", "number,title,mergedAt",
            "--limit", "20"
        ])
        if merged_json:
            try:
                prs = json.loads(merged_json)
            except json.JSONDecodeError:
                prs = []

    if prs:
        for pr in prs:
            merged_at = pr.get("mergedAt", "")
            if merged_at:
                events.append({
                    "date": merged_at,
                    "label": f"PR #{pr.get('number', 0)} merged",
                    "description": pr.get("title", "")[:50],
                    "category": "pr"
                })

    # Sort by date descending
    events.sort(key=lambda e: e.get("date", ""), reverse=True)

    return {
        "title": "Sprint Timeline (7 days)",
        "events": events[:30]
    }


def generate_sprint_progress(forecast_data: dict = None) -> dict:
    """Generate grip_progress from sprint forecast data.

    Args:
        forecast_data: Dict with predicted_velocity, confidence, trend, health_score.
                       If None, runs sprint_forecast against current repo.

    Returns:
        Dict for grip_progress: {title, style, tracks}
    """
    if forecast_data is None:
        try:
            from sprint_forecast import forecast_sprint
            result = forecast_sprint(".")
            forecast_data = {
                "predicted_velocity": result.predicted_velocity,
                "confidence_low": result.confidence_low,
                "confidence_high": result.confidence_high,
                "trend": result.trend,
                "health_score": result.health_score,
                "avg_velocity": result.avg_velocity,
            }
        except (ImportError, Exception):
            forecast_data = {"predicted_velocity": 0, "confidence_low": 0, "confidence_high": 0,
                             "trend": "stable", "health_score": 50, "avg_velocity": 0}

    health = forecast_data.get("health_score", 50)
    predicted = forecast_data.get("predicted_velocity", 0)
    avg = forecast_data.get("avg_velocity", predicted)
    trend = forecast_data.get("trend", "stable")

    trend_label = {"improving": "Trending Up", "declining": "Trending Down"}.get(trend, "Stable")

    return {
        "title": f"Sprint Forecast ({trend_label})",
        "style": "bar",
        "tracks": [
            {
                "id": "health",
                "label": "Sprint Health",
                "current": health,
                "total": 100,
                "unit": "%",
                "color": "#22c55e" if health >= 70 else "#eab308" if health >= 40 else "#ef4444",
                "milestones": [{"at": 40, "label": "At Risk"}, {"at": 70, "label": "Healthy"}, {"at": 90, "label": "Excellent"}],
            },
            {
                "id": "velocity",
                "label": "Predicted Velocity",
                "current": round(predicted),
                "total": round(max(predicted * 1.5, avg * 1.5, 1)),
                "unit": "pts",
                "color": "#3b82f6",
                "milestones": [{"at": round(avg), "label": f"Avg ({round(avg)})"}] if avg else [],
            },
            {
                "id": "confidence",
                "label": "Confidence Range",
                "current": round(forecast_data.get("confidence_high", 0) - forecast_data.get("confidence_low", 0)),
                "total": round(max(predicted * 2, 1)),
                "unit": "pts spread",
                "color": "#8b5cf6",
            },
        ],
    }
