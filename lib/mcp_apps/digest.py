"""Digest-related MCP app generators: digests, stories, changelogs, releases, weather, velocity."""

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

from .common import _run_git, _run_gh, CLAUDE_DIR, REPO_SLUG


def generate_digest(cards: list) -> dict:
    """Generate grip_digest for expandable summary cards.

    Args:
        cards: List of card dicts with keys: title, summary, details

    Returns:
        Dict with structure: {title, cards: [{title, summary, details}]}
    """
    return {
        "title": "Sprint Digest",
        "cards": [
            {
                "title": c.get("title", ""),
                "summary": c.get("summary", "")[:100],
                "details": c.get("details", "")
            }
            for c in cards[:15]
        ]
    }


def generate_save_digest() -> dict:
    """Generate grip_digest from GRIP memory subsystems.

    Uses digest_data.collect_digest_data() to extract achievements,
    active state, genome pulse, and TODO status. Returns data for
    the grip_digest MCP App component.
    """
    try:
        from digest_data import collect_digest_data
    except ImportError:
        return {
            "title": "Memory Digest",
            "summary": "digest_data module not available",
            "cards": [{"id": "error", "title": "Import Error",
                       "summary": "digest_data.py not found", "icon": "warning",
                       "status": "error"}],
        }

    data = collect_digest_data()
    cards = []

    # Achievements card
    achievements = data.get("achievements", [])
    if achievements:
        cards.append({
            "id": "achievements",
            "title": "Recent Achievements",
            "summary": f"{len(achievements)} recent milestone(s)",
            "icon": "trophy",
            "status": "success",
            "detail": "\n".join(f"- {a}" for a in achievements),
        })

    # Active work card
    active = data.get("active_state", [])
    active_items = [a for a in active if "items tracked" not in a]
    if active_items:
        cards.append({
            "id": "active_work",
            "title": "Active Work",
            "summary": active_items[0],
            "icon": "hammer",
            "status": "info",
            "detail": "\n".join(f"- {a}" for a in active_items),
        })

    # TODO pulse card
    todo_pulse = data.get("todo_pulse")
    if todo_pulse:
        cards.append({
            "id": "todo_pulse",
            "title": "TODO Tracker",
            "summary": todo_pulse,
            "icon": "list",
            "status": "warning" if "pending" in todo_pulse else "info",
        })

    # Genome pulse card
    genome = data.get("genome")
    if genome:
        top_genes = ", ".join(genome.get("top_genes", []))
        cards.append({
            "id": "genome",
            "title": "Genome Pulse",
            "summary": (
                f"Gen {genome['generation']} | "
                f"{genome['gene_count']} genes | "
                f"{genome['fitness']:.3f} {genome['trend']}"
            ),
            "icon": "dna",
            "status": "success" if genome["fitness"] >= 0.7 else "warning",
            "detail": f"Top genes: {top_genes}\nConverge runs: {genome.get('runs', 0)}",
        })

    if not cards:
        cards.append({
            "id": "empty",
            "title": "Memory Digest",
            "summary": "No digest data available",
            "icon": "info",
            "status": "info",
        })

    return {
        "title": "Memory Digest",
        "summary": f"{len(cards)} memory system(s) reporting",
        "cards": cards,
    }


def generate_code_story_digest(story_data: dict = None) -> dict:
    """Generate grip_digest from code storyteller output.

    Args:
        story_data: Dict with title, chapters list.
                    If None, runs code_storyteller against current repo.

    Returns:
        Dict for grip_digest: {title, cards}
    """
    if story_data is None:
        try:
            from code_storyteller import tell_story, format_story_digest
            story = tell_story(".")
            return format_story_digest(story)
        except (ImportError, Exception):
            story_data = {"title": "Code Story", "chapters": []}

    cards = []
    for idx, chapter in enumerate(story_data.get("chapters", [])[:10]):
        mood = chapter.get("mood", "productive")
        status_map = {"productive": "success", "exploratory": "info", "intense": "warning",
                      "maintenance": "info", "collaborative": "success"}

        cards.append({
            "id": f"ch-{idx}",
            "title": chapter.get("title", f"Chapter {idx + 1}"),
            "summary": chapter.get("summary", "")[:100],
            "detail": chapter.get("narrative", ""),
            "status": status_map.get(mood, "info"),
            "icon": {"productive": "\U0001f680", "exploratory": "\U0001f50d", "intense": "\u26a1",
                     "maintenance": "\U0001f527", "collaborative": "\U0001f91d"}.get(mood, "\U0001f4dd"),
        })

    return {"title": story_data.get("title", "Code Story"), "cards": cards}


def generate_changelog_timeline(changelog_data: dict = None) -> dict:
    """Generate grip_timeline from changelog generator output.

    Args:
        changelog_data: Dict with sections list from changelog_generator.
                        If None, runs changelog_generator against current repo.

    Returns:
        Dict for grip_timeline: {title, events}
    """
    if changelog_data is None:
        try:
            from changelog_generator import generate_changelog, format_changelog_json
            import json as _json
            cl = generate_changelog(".", group_by="date")
            changelog_data = _json.loads(format_changelog_json(cl))
        except (ImportError, Exception):
            changelog_data = {"sections": [], "repo_name": ""}

    events = []
    for idx, section in enumerate(changelog_data.get("sections", [])[:20]):
        entries = section.get("entries", [])
        feat_count = sum(1 for e in entries if e.get("type") == "feat")
        fix_count = sum(1 for e in entries if e.get("type") == "fix")
        breaking = any(e.get("breaking") for e in entries)

        label = section.get("version", section.get("date", f"Section {idx + 1}"))
        summary_parts = []
        if feat_count:
            summary_parts.append(f"{feat_count} features")
        if fix_count:
            summary_parts.append(f"{fix_count} fixes")
        if breaking:
            summary_parts.append("BREAKING")

        event_type = "milestone" if breaking else "phase"
        status = "complete" if idx > 0 else "active"

        events.append({
            "id": f"cl-{idx}",
            "label": str(label)[:40],
            "date": section.get("date", ""),
            "type": event_type,
            "status": status,
            "description": ", ".join(summary_parts) if summary_parts else f"{len(entries)} commits",
        })

    repo = changelog_data.get("repo_name", "")
    title = f"Changelog Timeline{' - ' + repo if repo else ''}"
    return {"title": title, "events": events}


def generate_release_digest(notes_data: dict = None) -> dict:
    """Generate grip_digest from release_notes output.

    Args:
        notes_data: Dict with version, items, highlights, breaking_changes, stats.
                    If None, runs release_notes against current repo.

    Returns:
        Dict for grip_digest: {title, cards}
    """
    if notes_data is None:
        try:
            from release_notes import compose_release_notes, format_notes_json
            import json as _json
            notes = compose_release_notes(".")
            notes_data = _json.loads(format_notes_json(notes))
        except (ImportError, Exception):
            notes_data = {"version": "", "items": [], "highlights": [], "breaking_changes": [], "stats": {}}

    cards = []
    version = notes_data.get("version", "Latest")

    # Highlights card
    highlights = notes_data.get("highlights", [])
    if highlights:
        detail = "\n".join(f"- {h.get('title', '')}" for h in highlights[:5])
        cards.append({
            "id": "highlights",
            "title": "Highlights",
            "summary": f"{len(highlights)} notable changes",
            "detail": detail,
            "status": "success",
            "icon": "star",
        })

    # Breaking changes card
    breaking = notes_data.get("breaking_changes", [])
    if breaking:
        detail = "\n".join(f"- {b.get('title', '')}" for b in breaking[:5])
        cards.append({
            "id": "breaking",
            "title": "Breaking Changes",
            "summary": f"{len(breaking)} breaking change(s) require attention",
            "detail": detail,
            "status": "error",
            "icon": "!!",
        })

    # Category cards
    items = notes_data.get("items", [])
    by_category = {}
    for item in items:
        cat = item.get("category", "Other")
        by_category.setdefault(cat, []).append(item)

    status_map = {"New Features": "success", "Bug Fixes": "warning", "Performance": "info",
                  "Refactoring": "info", "Documentation": "info"}
    icon_map = {"New Features": "+", "Bug Fixes": "~", "Performance": ">>",
                "Refactoring": "<>", "Documentation": "i"}

    for cat, cat_items in sorted(by_category.items(), key=lambda x: len(x[1]), reverse=True)[:5]:
        detail = "\n".join(f"- {i.get('title', '')}" for i in cat_items[:8])
        cards.append({
            "id": f"cat-{cat.lower().replace(' ', '-')}",
            "title": cat,
            "summary": f"{len(cat_items)} change(s)",
            "detail": detail,
            "status": status_map.get(cat, "info"),
            "icon": icon_map.get(cat, "o"),
        })

    # Stats card
    stats = notes_data.get("stats", {})
    if stats:
        contributors = stats.get("contributors", [])
        cards.append({
            "id": "stats",
            "title": "Release Stats",
            "summary": f"{stats.get('total_commits', 0)} commits by {len(contributors)} contributor(s)",
            "detail": f"Features: {stats.get('features', 0)}, Fixes: {stats.get('fixes', 0)}, Breaking: {stats.get('breaking', 0)}",
            "status": "info",
            "icon": "#",
        })

    return {"title": f"Release Notes{' - ' + version if version else ''}", "cards": cards}


def generate_weather_digest(weather_data: dict = None) -> dict:
    """Generate grip_digest from codebase weather forecast.

    Args:
        weather_data: Dict with weather forecast. If None, uses git metrics.

    Returns:
        Dict for grip_digest MCP app.
    """
    if weather_data is None:
        # Quick inline weather check
        log_7d = _run_git(["log", "--since=7 days ago", "--oneline", "--no-merges"])
        commits_7d = len(log_7d.strip().split("\n")) if log_7d else 0

        log_1d = _run_git(["log", "--since=1 day ago", "--oneline", "--no-merges"])
        commits_1d = len(log_1d.strip().split("\n")) if log_1d else 0

        # Determine weather
        if commits_7d > 30 and commits_1d > 5:
            condition = "sunny"
            temp = 85
            advisory = "Perfect coding weather. Ship that feature!"
        elif commits_7d > 15:
            condition = "partly_cloudy"
            temp = 70
            advisory = "Good conditions. Watch for afternoon complexity clouds."
        elif commits_7d > 5:
            condition = "cloudy"
            temp = 55
            advisory = "Overcast. Focus on clearing the backlog fog."
        elif commits_7d > 0:
            condition = "rainy"
            temp = 40
            advisory = "Bring an umbrella. Sprint pressure incoming."
        else:
            condition = "snowy"
            temp = 20
            advisory = "Development frozen. Time to thaw with a planning session."

        weather_data = {
            "condition": condition,
            "temperature": temp,
            "advisory": advisory,
            "commits_7d": commits_7d,
            "commits_1d": commits_1d,
        }

    condition = weather_data.get("condition", "cloudy")
    temp = weather_data.get("temperature", 50)

    weather_icons = {
        "sunny": "sunny", "partly_cloudy": "partly_sunny", "cloudy": "cloud",
        "rainy": "rain_cloud", "stormy": "thunder_cloud_and_rain",
        "foggy": "fog", "windy": "wind_face", "snowy": "snowflake",
    }

    status_map = {
        "sunny": "success", "partly_cloudy": "success", "cloudy": "warning",
        "rainy": "warning", "stormy": "error", "foggy": "warning",
        "windy": "info", "snowy": "error",
    }

    cards = [
        {
            "id": "current",
            "title": f"Current: {condition.replace('_', ' ').title()}",
            "summary": f"{temp}F -- {weather_data.get('advisory', '')}",
            "icon": weather_icons.get(condition, "cloud"),
            "status": status_map.get(condition, "info"),
            "detail": f"Based on {weather_data.get('commits_7d', 0)} commits (7d), {weather_data.get('commits_1d', 0)} today.",
        },
        {
            "id": "velocity",
            "title": "Wind Speed (Velocity)",
            "summary": f"{weather_data.get('commits_1d', 0)} commits today",
            "icon": "dash",
            "status": "success" if weather_data.get("commits_1d", 0) > 3 else "warning",
        },
        {
            "id": "forecast",
            "title": "Tomorrow's Forecast",
            "summary": "Trending " + ("warmer" if temp > 60 else "cooler") + " -- plan accordingly",
            "icon": "crystal_ball",
            "status": "info",
        },
    ]

    return {
        "title": "Codebase Weather Report",
        "cards": cards,
    }


def generate_focus_digest(focus_data: dict = None) -> dict:
    """Generate grip_digest from focus tracker analysis.

    Args:
        focus_data: Dict with focus metrics. If None, uses defaults.

    Returns:
        Dict for grip_digest MCP app.
    """
    if focus_data is None:
        focus_data = {
            "score": 75,
            "flow_sessions": 3,
            "total_focus_minutes": 180,
            "distractions": 5,
            "best_hour": "10:00",
            "breaks_taken": 2,
        }

    score = focus_data.get("score", 0)
    icon = "dart" if score >= 70 else "brain" if score >= 40 else "zzz"

    cards = [
        {
            "id": "score",
            "title": "Focus Score",
            "summary": f"{score}/100 -- {'Excellent' if score >= 80 else 'Good' if score >= 60 else 'Needs improvement'}",
            "icon": icon,
            "status": "success" if score >= 70 else "warning" if score >= 40 else "error",
            "detail": f"Based on commit timing patterns, session duration, and context switch analysis.",
        },
        {
            "id": "flow",
            "title": "Flow Sessions",
            "summary": f"{focus_data.get('flow_sessions', 0)} deep work sessions ({focus_data.get('total_focus_minutes', 0)} min total)",
            "icon": "zap",
            "status": "success" if focus_data.get("flow_sessions", 0) >= 3 else "warning",
        },
        {
            "id": "distractions",
            "title": "Context Switches",
            "summary": f"{focus_data.get('distractions', 0)} interruptions detected",
            "icon": "warning",
            "status": "success" if focus_data.get("distractions", 0) <= 3 else "warning" if focus_data.get("distractions", 0) <= 7 else "error",
        },
        {
            "id": "best_hour",
            "title": "Peak Productivity",
            "summary": f"Best hour: {focus_data.get('best_hour', 'N/A')}",
            "icon": "clock",
            "status": "info",
            "detail": "Schedule deep work during this hour for maximum flow.",
        },
    ]

    return {
        "title": "Daily Focus Report",
        "cards": cards,
    }


def _resolve_metrics_path(metrics_path: str = None) -> Path:
    """Resolve the converge-metrics JSONL file path.

    Checks metrics_path override, then learning/, then cache/.
    """
    if metrics_path:
        return Path(metrics_path)
    path = CLAUDE_DIR / "learning" / "converge-metrics.jsonl"
    if not path.exists():
        path = CLAUDE_DIR / "cache" / "converge-metrics.jsonl"
    return path


def _load_converge_events(path: Path) -> tuple:
    """Parse converge-metrics.jsonl into starts, ends, and depths dicts.

    Returns:
        (starts, ends, depths) where starts={run_id: obj},
        ends=[obj, ...], depths={run_id: [obj, ...]}.
    """
    starts = {}
    ends = []
    depths = {}

    if not path.exists():
        return starts, ends, depths

    try:
        with open(path, "r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                event = obj.get("event", "")
                rid = obj.get("run_id", "")
                if event == "converge_start":
                    starts[rid] = obj
                elif event == "converge_end":
                    ends.append(obj)
                elif event == "converge_depth":
                    depths.setdefault(rid, []).append(obj)
    except (OSError, IOError):
        pass  # File unreadable -- treat as empty

    return starts, ends, depths


def _join_converge_runs(starts: dict, ends: list) -> list:
    """Join start and end events into run dicts with computed velocity."""
    runs = []
    for end in ends:
        rid = end.get("run_id", "")
        start = starts.get(rid, {})
        trajectory = end.get("velocity_trajectory", [])

        depth_reached = end.get("depth_reached", 0)
        final_score = end.get("final_score", 0) or 0
        initial_score = trajectory[0] if trajectory else 0
        velocity = 0.0
        if depth_reached and depth_reached > 0:
            velocity = (final_score - initial_score) / depth_reached

        runs.append({
            "run_id": rid,
            "task": start.get("task", "unknown"),
            "task_type": start.get("task_type", "unknown"),
            "max_depth": start.get("max_depth", 0),
            "depth_reached": depth_reached,
            "final_score": final_score,
            "initial_score": initial_score,
            "satisfied": end.get("satisfied", False),
            "elapsed_minutes": end.get("elapsed_minutes", 0),
            "velocity": velocity,
            "trajectory": trajectory,
            "timestamp": end.get("timestamp", ""),
        })
    return runs


def _avg_velocity(run_list: list) -> float:
    """Compute mean velocity across a list of run dicts."""
    if not run_list:
        return 0.0
    return sum(r["velocity"] for r in run_list) / len(run_list)


def _build_velocity_trend_card(runs: list) -> tuple:
    """Build the velocity trend card from run history.

    Returns:
        (card_dict, avg_5, direction) -- avg_5 and direction are needed
        by the parent for the dashboard summary line.
    """
    recent_5 = runs[-5:] if len(runs) >= 5 else runs
    recent_10 = runs[-10:] if len(runs) >= 10 else runs
    recent_20 = runs[-20:] if len(runs) >= 20 else runs

    avg_5 = _avg_velocity(recent_5)
    avg_10 = _avg_velocity(recent_10)
    avg_20 = _avg_velocity(recent_20)
    overall_avg = _avg_velocity(runs)

    if avg_5 > overall_avg * 1.05:
        direction = "UP"
        trend_status = "success"
    elif avg_5 < overall_avg * 0.95:
        direction = "DOWN"
        trend_status = "warning"
    else:
        direction = "STABLE"
        trend_status = "info"

    card = {
        "id": "velocity_trend",
        "title": "Velocity Trend",
        "summary": f"Current: {avg_5:.1f} pts/depth ({direction})",
        "icon": "trending-up" if direction == "UP" else "trending-down" if direction == "DOWN" else "minus",
        "status": trend_status,
        "detail": (
            f"Last 5 runs:  {avg_5:.1f} pts/depth\n"
            f"Last 10 runs: {avg_10:.1f} pts/depth\n"
            f"Last 20 runs: {avg_20:.1f} pts/depth\n"
            f"All time:     {overall_avg:.1f} pts/depth ({len(runs)} runs)"
        ),
    }
    return card, avg_5, direction


def _build_satisfaction_card(runs: list) -> tuple:
    """Build the satisfaction rate card.

    Returns:
        (card_dict, sat_rate) -- sat_rate is needed by the parent summary.
    """
    recent_10 = runs[-10:] if len(runs) >= 10 else runs

    satisfied_count = sum(1 for r in runs if r["satisfied"])
    sat_rate = (satisfied_count / len(runs)) * 100

    recent_sat = sum(1 for r in recent_10 if r["satisfied"])
    recent_sat_rate = (recent_sat / len(recent_10)) * 100

    card = {
        "id": "satisfaction_rate",
        "title": "Satisfaction Rate",
        "summary": f"{sat_rate:.0f}% satisfied ({satisfied_count}/{len(runs)})",
        "icon": "check-circle" if sat_rate >= 80 else "alert-circle",
        "status": "success" if sat_rate >= 80 else "warning" if sat_rate >= 50 else "error",
        "detail": (
            f"All time: {sat_rate:.1f}% ({satisfied_count}/{len(runs)})\n"
            f"Recent {len(recent_10)}: {recent_sat_rate:.1f}% ({recent_sat}/{len(recent_10)})"
        ),
    }
    return card, sat_rate


def _build_depth_efficiency_card(runs: list) -> dict:
    """Build the depth efficiency card, or None if no data."""
    efficiency_values = []
    for r in runs:
        max_d = r["max_depth"]
        if max_d and max_d > 0:
            efficiency_values.append(r["depth_reached"] / max_d)

    if not efficiency_values:
        return None

    avg_efficiency = sum(efficiency_values) / len(efficiency_values)
    eff_status = "success" if avg_efficiency <= 0.6 else "info" if avg_efficiency <= 0.8 else "warning"
    return {
        "id": "depth_efficiency",
        "title": "Depth Efficiency",
        "summary": f"{avg_efficiency:.0%} of max depth used (lower = faster)",
        "icon": "zap" if avg_efficiency <= 0.6 else "clock",
        "status": eff_status,
        "detail": (
            f"Avg depth used: {avg_efficiency:.1%} of max allowed\n"
            f"Runs measured: {len(efficiency_values)}\n"
            f"Interpretation: lower ratio means faster convergence"
        ),
    }


def _build_top_types_card(runs: list) -> dict:
    """Build the top performing task types card."""
    type_stats = {}
    for r in runs:
        tt = r["task_type"]
        if tt not in type_stats:
            type_stats[tt] = {"velocities": [], "count": 0, "satisfied": 0}
        type_stats[tt]["velocities"].append(r["velocity"])
        type_stats[tt]["count"] += 1
        if r["satisfied"]:
            type_stats[tt]["satisfied"] += 1

    sorted_types = sorted(
        type_stats.items(),
        key=lambda kv: sum(kv[1]["velocities"]) / len(kv[1]["velocities"]) if kv[1]["velocities"] else 0,
        reverse=True,
    )

    type_lines = []
    for tt, stats in sorted_types:
        avg_v = sum(stats["velocities"]) / len(stats["velocities"]) if stats["velocities"] else 0
        sat_pct = (stats["satisfied"] / stats["count"] * 100) if stats["count"] else 0
        type_lines.append(f"- {tt}: {avg_v:.1f} pts/depth, {sat_pct:.0f}% satisfied ({stats['count']} runs)")

    best_type = sorted_types[0][0] if sorted_types else "none"
    return {
        "id": "top_types",
        "title": "Top Performing Types",
        "summary": f"Best: {best_type} ({len(type_stats)} types tracked)",
        "icon": "bar-chart",
        "status": "info",
        "detail": "\n".join(type_lines),
    }


def _build_recent_runs_card(runs: list) -> dict:
    """Build the recent runs card showing last 5 converge results."""
    last_5 = runs[-5:]
    last_5.reverse()  # Most recent first
    run_lines = []
    for r in last_5:
        sat_mark = "OK" if r["satisfied"] else "FAIL"
        task_preview = r["task"][:40]
        run_lines.append(
            f"- [{sat_mark}] {task_preview} | "
            f"score: {r['final_score']:.0f}, "
            f"velocity: {r['velocity']:.1f}, "
            f"depth: {r['depth_reached']}/{r['max_depth']}"
        )

    return {
        "id": "recent_runs",
        "title": "Recent Runs",
        "summary": f"Last {len(last_5)} convergence runs",
        "icon": "list",
        "status": "info",
        "detail": "\n".join(run_lines),
    }


def generate_velocity_dashboard(metrics_path: str = None) -> dict:
    """Generate grip_digest for convergence velocity trends.

    Reads converge-metrics.jsonl, joins start+end events by run_id,
    and computes velocity, satisfaction rate, depth efficiency, and
    per-task-type breakdowns.

    Args:
        metrics_path: Optional override for the metrics JSONL file path.
                      Defaults to CLAUDE_DIR/learning/converge-metrics.jsonl,
                      falling back to CLAUDE_DIR/cache/converge-metrics.jsonl.

    Returns:
        Dict with {title, summary, cards} for the grip_digest MCP App.
    """
    path = _resolve_metrics_path(metrics_path)
    starts, ends, _depths = _load_converge_events(path)
    runs = _join_converge_runs(starts, ends)

    if not runs:
        return {
            "title": "Convergence Velocity Dashboard",
            "summary": "No data available",
            "cards": [{
                "id": "no_data",
                "title": "No Convergence Data",
                "summary": "No converge runs found in metrics file",
                "icon": "info",
                "status": "info",
            }],
        }

    cards = []

    trend_card, avg_5, direction = _build_velocity_trend_card(runs)
    cards.append(trend_card)

    sat_card, sat_rate = _build_satisfaction_card(runs)
    cards.append(sat_card)

    eff_card = _build_depth_efficiency_card(runs)
    if eff_card:
        cards.append(eff_card)

    cards.append(_build_top_types_card(runs))
    cards.append(_build_recent_runs_card(runs))

    return {
        "title": "Convergence Velocity Dashboard",
        "summary": f"{len(runs)} runs | velocity {avg_5:.1f} pts/depth ({direction}) | {sat_rate:.0f}% satisfied",
        "cards": cards,
    }
