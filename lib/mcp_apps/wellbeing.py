"""Wellbeing and achievement MCP app generators: mood, achievements, focus, routine, scorecard."""

import json
from datetime import datetime, timezone, timedelta

from .common import _run_git, CLAUDE_DIR


def generate_mood_timeline(mood_data: dict = None) -> dict:
    """Generate grip_timeline from commit mood analysis.

    Args:
        mood_data: Dict with mood history. If None, runs commit_mood CLI.

    Returns:
        Dict for grip_timeline MCP app.
    """
    if mood_data is None:
        output = _run_git(["log", "--oneline", "--since=7 days ago", "--no-merges"])
        if not output:
            mood_data = {"daily_moods": []}
        else:
            # Simple inline mood detection
            mood_data = {"daily_moods": []}
            mood_keywords = {
                "energized": ["feat", "add", "implement", "create", "build", "ship"],
                "focused": ["refactor", "optimize", "improve", "clean", "update"],
                "frustrated": ["fix", "bug", "hotfix", "revert", "broken", "hack"],
                "creative": ["design", "prototype", "experiment", "explore", "new"],
                "maintenance": ["chore", "deps", "bump", "config", "ci", "docs"],
            }
            commits = output.strip().split("\n")
            for commit in commits[-7:]:
                msg = commit.lower()
                mood = "focused"
                for m, keywords in mood_keywords.items():
                    if any(k in msg for k in keywords):
                        mood = m
                        break
                mood_data["daily_moods"].append({"mood": mood, "message": commit[:60]})

    events = []
    today = datetime.now(timezone.utc)
    for i, entry in enumerate(mood_data.get("daily_moods", [])[:7]):
        mood = entry.get("mood", "focused")
        day = today - timedelta(days=len(mood_data.get("daily_moods", [])) - 1 - i)
        events.append({
            "id": f"mood_{i}",
            "label": f"{mood.title()}: {entry.get('message', '')[:40]}",
            "date": day.strftime("%Y-%m-%d"),
            "type": "milestone",
            "status": "complete" if i < len(mood_data.get("daily_moods", [])) - 1 else "active",
        })

    return {
        "title": "Developer Mood Timeline (7 days)",
        "events": events,
        "todayMarker": True,
    }


def generate_achievement_progress(badge_data: dict = None) -> dict:
    """Generate grip_progress from achievement badges.

    Args:
        badge_data: Dict with badge counts by tier. If None, uses defaults.

    Returns:
        Dict for grip_progress MCP app.
    """
    if badge_data is None:
        # Try to load from state file
        state_file = CLAUDE_DIR / "data" / "achievement_state.json"
        if state_file.exists():
            try:
                with open(state_file) as f:
                    badge_data = json.load(f)
            except (json.JSONDecodeError, OSError):
                badge_data = {}
        else:
            badge_data = {}

    tracks = []

    # Streak track
    streak = badge_data.get("current_streak", 0)
    tracks.append({
        "id": "streak",
        "label": "Commit Streak",
        "current": min(streak, 30),
        "total": 30,
        "unit": "days",
        "color": "#f59e0b",
        "milestones": [
            {"at": 7, "label": "Week Warrior"},
            {"at": 14, "label": "Fortnight Force"},
            {"at": 30, "label": "Monthly Master"},
        ],
    })

    # Badge collection track
    total_badges = badge_data.get("total_badges", 0)
    tracks.append({
        "id": "badges",
        "label": "Badges Earned",
        "current": min(total_badges, 50),
        "total": 50,
        "unit": "badges",
        "color": "#8b5cf6",
        "milestones": [
            {"at": 10, "label": "Collector"},
            {"at": 25, "label": "Veteran"},
            {"at": 50, "label": "Legend"},
        ],
    })

    # Tier progress
    tiers = badge_data.get("tiers", {"bronze": 0, "silver": 0, "gold": 0, "platinum": 0})
    for tier_name, count in tiers.items():
        max_val = {"bronze": 15, "silver": 15, "gold": 15, "platinum": 15}.get(tier_name, 15)
        colors = {"bronze": "#cd7f32", "silver": "#c0c0c0", "gold": "#ffd700", "platinum": "#e5e4e2"}
        tracks.append({
            "id": f"tier_{tier_name}",
            "label": f"{tier_name.title()} Badges",
            "current": min(count, max_val),
            "total": max_val,
            "unit": "badges",
            "color": colors.get(tier_name, "#6b7280"),
        })

    return {
        "title": "Achievement Progress",
        "style": "bar",
        "tracks": tracks,
    }


def generate_focus_cards_board() -> dict:
    """Generate grip_kanban from Focus Cards state.

    Reads .focus-cards-state.json and returns a Kanban board structure
    matching the grip_kanban MCP app schema. Includes rich card detail
    with checklists, progress, branch info, and colour-coded columns.

    Returns:
        Dict for grip_kanban MCP app.
    """
    try:
        from focus_cards import FocusCardsManager
        mgr = FocusCardsManager()
        board = mgr.export_board()
        stats = mgr.stats()
        total = stats.get("total", 0)
        active = stats.get("active", 0)
        subtitle = f"{active} active of {total} cards" if total > 0 else "No cards yet"
        return {"title": "Focus Cards", "subtitle": subtitle, **board}
    except Exception:
        return {
            "title": "Focus Cards",
            "subtitle": "No cards yet",
            "columns": [
                {"id": "active", "heading": "In Progress", "color": "#22c55e", "cards": []},
                {"id": "pinned", "heading": "Pinned", "color": "#eab308", "cards": []},
                {"id": "paused", "heading": "Paused", "color": "#94a3b8", "cards": []},
                {"id": "completed", "heading": "Done", "color": "#3b82f6", "cards": []},
            ],
        }


def generate_routine_timeline(routine_data: dict = None) -> dict:
    """Generate grip_timeline data for daily routine visualization.

    Args:
        routine_data: Dict with phases (peak hours) and activities (predicted tasks).
                      If None, tries to import from routine_predictor.

    Returns:
        Dict for grip_timeline: {title, events}
    """
    if routine_data is None:
        try:
            from routine_predictor import predict_routine
            routine_data = predict_routine()
        except (ImportError, Exception):
            routine_data = {"phases": [], "activities": []}

    now_hour = datetime.now().hour
    events = []

    for phase in routine_data.get("phases", []):
        start_hour = phase.get("start_hour", 0)
        end_hour = phase.get("end_hour", 0)
        label = phase.get("label", f"Phase {start_hour}-{end_hour}")

        if start_hour <= now_hour < end_hour:
            status = "active"
        elif start_hour > now_hour:
            status = "upcoming"
        else:
            status = "complete"

        events.append({
            "id": f"phase-{start_hour}",
            "label": label,
            "date": f"{start_hour:02d}:00-{end_hour:02d}:00",
            "type": "phase",
            "status": status,
            "description": phase.get("description", ""),
        })

    for activity in routine_data.get("activities", []):
        hour = activity.get("hour", 0)
        if hour > now_hour:
            status = "upcoming"
        elif hour == now_hour:
            status = "active"
        else:
            status = "complete"

        events.append({
            "id": f"activity-{hour}",
            "label": activity.get("label", "Activity"),
            "date": f"{hour:02d}:00",
            "type": "milestone",
            "status": status,
            "description": activity.get("description", ""),
        })

    events.sort(key=lambda e: e.get("date", ""))

    return {
        "title": "Daily Routine Timeline",
        "events": events,
    }


def generate_scorecard_rubric(scorecard_data: dict = None) -> dict:
    """Generate grip_scoring_rubric from project scorecard.

    Args:
        scorecard_data: Dict with dimension scores. If None, uses defaults.

    Returns:
        Dict for grip_scoring_rubric MCP app.
    """
    if scorecard_data is None:
        scorecard_data = {
            "dimensions": {
                "code_quality": 80,
                "velocity": 75,
                "debt": 60,
                "documentation": 55,
                "security": 85,
                "team_health": 70,
                "innovation": 65,
            }
        }

    criteria = []
    dimensions = scorecard_data.get("dimensions", {})

    dim_labels = {
        "code_quality": "Code Quality",
        "velocity": "Velocity",
        "debt": "Tech Debt",
        "documentation": "Documentation",
        "security": "Security",
        "team_health": "Team Health",
        "innovation": "Innovation",
    }

    for dim_id, label in dim_labels.items():
        criteria.append({
            "id": dim_id,
            "label": label,
            "scale": 5,
            "labels": ["Critical", "Poor", "Fair", "Good", "Excellent"],
        })

    return {
        "title": "Project Health Scorecard",
        "subject": "GRIP Framework",
        "criteria": criteria,
        "tiers": [
            {"label": "Platinum", "minScore": 90, "color": "#e5e4e2"},
            {"label": "Gold", "minScore": 75, "color": "#ffd700"},
            {"label": "Silver", "minScore": 60, "color": "#c0c0c0"},
            {"label": "Bronze", "minScore": 40, "color": "#cd7f32"},
            {"label": "Needs Work", "minScore": 0, "color": "#ef4444"},
        ],
    }


def generate_session_achievements_progress(achievements_data: dict = None) -> dict:
    """Generate grip_progress data for session achievement tracking.

    Args:
        achievements_data: Dict with tracks like commits, tests_added, features, fixes, docs.
                           Each track has current/total counts.
                           If None, tries to import from session_achievements and analyze last 8 hours.

    Returns:
        Dict for grip_progress: {title, style, tracks}
    """
    if achievements_data is None:
        try:
            from session_achievements import analyze_session
            achievements_data = analyze_session(hours=8)
        except (ImportError, Exception):
            achievements_data = {
                "commits": {"current": 0, "total": 10},
                "tests_added": {"current": 0, "total": 20},
                "features": {"current": 0, "total": 5},
                "fixes": {"current": 0, "total": 5},
                "docs": {"current": 0, "total": 3},
            }

    track_config = {
        "commits": {"label": "Commits", "color": "#3b82f6", "unit": "commits",
                     "milestones": [{"at": 5, "label": "Warm-up"}, {"at": 10, "label": "Productive"}]},
        "tests_added": {"label": "Tests Added", "color": "#22c55e", "unit": "tests",
                        "milestones": [{"at": 10, "label": "Covered"}, {"at": 20, "label": "Thorough"}]},
        "features": {"label": "Features", "color": "#8b5cf6", "unit": "features",
                     "milestones": [{"at": 3, "label": "Momentum"}, {"at": 5, "label": "Sprint Goal"}]},
        "fixes": {"label": "Bug Fixes", "color": "#f97316", "unit": "fixes",
                  "milestones": [{"at": 3, "label": "Stable"}, {"at": 5, "label": "Clean"}]},
        "docs": {"label": "Documentation", "color": "#eab308", "unit": "docs",
                 "milestones": [{"at": 2, "label": "Documented"}, {"at": 3, "label": "Complete"}]},
    }

    tracks = []
    for key, config in track_config.items():
        data = achievements_data.get(key, {})
        current = data.get("current", 0)
        total = data.get("total", 1)
        tracks.append({
            "id": key,
            "label": config["label"],
            "current": current,
            "total": max(total, 1),
            "unit": config["unit"],
            "color": config["color"],
            "milestones": config["milestones"],
        })

    return {
        "title": "Session Achievements",
        "style": "ring",
        "tracks": tracks,
    }
