"""CLI entry point for MCP app generator."""

import json
import sys
from dataclasses import asdict

from .sprint import generate_sprint_kanban, generate_sprint_timeline, generate_sprint_progress
from .project import (
    generate_project_circuit, generate_session_circuit, generate_module_dependency_map,
    generate_dependency_circuit, generate_workflow_health_circuit,
)
from .backlog import generate_backlog_data_table, generate_memory_kanban, generate_tech_debt_kanban
from .review import (
    generate_pr_review_rubric, generate_retrospective, generate_pr_impact_rubric,
    generate_review_checklist_mcp,
)
from .analytics import (
    generate_session_analytics, generate_health_report, generate_progress_tracker,
    generate_contributor_data_table, generate_decision_health_circuit,
)
from .knowledge import (
    generate_knowledge_concept_map, generate_gap_visualiser, generate_idea_canvas,
    generate_complexity_concept_map, generate_collaboration_map, generate_doc_coverage_gap,
)
from .digest import (
    generate_digest, generate_save_digest, generate_code_story_digest,
    generate_changelog_timeline, generate_release_digest, generate_weather_digest,
    generate_focus_digest, generate_velocity_dashboard,
)
from .wellbeing import (
    generate_mood_timeline, generate_achievement_progress, generate_focus_cards_board,
    generate_routine_timeline, generate_scorecard_rubric, generate_session_achievements_progress,
)
from .tools import generate_smart_commit_options, generate_threat_matrix, generate_pair_suggestions_table


# Dispatch table: app_type -> zero-arg generator function
_DISPATCH = {
    "sprint_kanban": generate_sprint_kanban,
    "project_circuit": generate_project_circuit,
    "sprint_timeline": generate_sprint_timeline,
    "pr_rubric": generate_pr_review_rubric,
    "retrospective": generate_retrospective,
    "knowledge_map": generate_knowledge_concept_map,
    "tech_debt_board": generate_tech_debt_kanban,
    "sprint_progress": generate_sprint_progress,
    "dependency_dashboard": generate_dependency_circuit,
    "pr_impact_rubric": generate_pr_impact_rubric,
    "code_story": generate_code_story_digest,
    "changelog_timeline": generate_changelog_timeline,
    "review_checklist": generate_review_checklist_mcp,
    "release_digest": generate_release_digest,
    "doc_coverage_gap": generate_doc_coverage_gap,
    "complexity_map": generate_complexity_concept_map,
    "contributor_table": generate_contributor_data_table,
    "session_achievements": generate_session_achievements_progress,
    "smart_commit": generate_smart_commit_options,
    "routine_timeline": generate_routine_timeline,
    "mood_timeline": generate_mood_timeline,
    "achievement_progress": generate_achievement_progress,
    "focus_cards_board": generate_focus_cards_board,
    "focus_digest": generate_focus_digest,
    "collaboration_map": generate_collaboration_map,
    "weather_digest": generate_weather_digest,
    "scorecard_rubric": generate_scorecard_rubric,
    "pair_suggestions": generate_pair_suggestions_table,
    "session_circuit": generate_session_circuit,
    "module_deps": generate_module_dependency_map,
    "threat_matrix": generate_threat_matrix,
    "health_report": generate_health_report,
    "session_analytics": generate_session_analytics,
    "workflow_health": generate_workflow_health_circuit,
    "decision_health": generate_decision_health_circuit,
    "memory_kanban": generate_memory_kanban,
    "save_digest": generate_save_digest,
    "velocity_dashboard": generate_velocity_dashboard,
}

# App types that need special handling (not simple zero-arg calls)
_SPECIAL_COMMANDS = {"backlog_table", "idea_canvas", "gap_visualiser"}


def _print_usage():
    """Print CLI usage and exit."""
    app_types = sorted(list(_DISPATCH.keys()) + list(_SPECIAL_COMMANDS))
    print("Usage: mcp_app_generator.py <app_type> [--source <data_source>]")
    # Print in rows of 4 for readability
    for i in range(0, len(app_types), 4):
        chunk = ", ".join(app_types[i:i + 4])
        prefix = "App types: " if i == 0 else "           "
        suffix = "," if i + 4 < len(app_types) else ""
        print(f"{prefix}{chunk}{suffix}")
    sys.exit(1)


def _parse_args():
    """Parse CLI arguments. Returns (app_type, source)."""
    if len(sys.argv) < 2:
        _print_usage()
    app_type = sys.argv[1]
    source = sys.argv[3] if len(sys.argv) > 3 and sys.argv[2] == "--source" else None
    return app_type, source


def _run_backlog_table():
    """Generate backlog table from backlog_prioritiser output."""
    try:
        from backlog_prioritiser import rank_backlog
        scored = rank_backlog()
        return generate_backlog_data_table([asdict(s) if hasattr(s, '__dict__') else s for s in scored])
    except ImportError:
        print("Error: backlog_prioritiser.py not available")
        sys.exit(1)


def _run_idea_canvas():
    """Generate idea canvas with sample demo data."""
    sample_ideas = [
        {"id": "1", "title": "Add mobile UI", "description": "Mobile-responsive dashboard", "impact": "high", "effort": "low", "status": "proposed"},
        {"id": "2", "title": "Refactor hooks", "description": "Clean up hook architecture", "impact": "low", "effort": "high", "status": "proposed"},
    ]
    return generate_idea_canvas(sample_ideas)


def _run_gap_visualiser():
    """Generate gap visualiser with sample demo data."""
    sample_analysis = {
        "coverage_by_domain": {
            "API Design": {"current": 2, "target": 5},
            "Testing": {"current": 8, "target": 10},
        },
        "missing_skills": ["API design patterns", "GraphQL"]
    }
    return generate_gap_visualiser(sample_analysis)


def _dispatch(app_type):
    """Dispatch app_type to the correct generator. Returns result or exits."""
    # Check special commands first
    special_handlers = {
        "backlog_table": _run_backlog_table,
        "idea_canvas": _run_idea_canvas,
        "gap_visualiser": _run_gap_visualiser,
    }
    handler = special_handlers.get(app_type) or _DISPATCH.get(app_type)
    if handler is None:
        print(f"Unknown app type: {app_type}")
        sys.exit(1)
    return handler()


def main():
    """CLI entry point."""
    app_type, _source = _parse_args()
    result = _dispatch(app_type)
    if result:
        print(json.dumps(result, indent=2))
