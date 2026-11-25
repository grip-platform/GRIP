"""MCP Apps Data Generator package for GRIP.

Generates structured data for all GRIP MCP App components from GRIP data sources.
Each generator returns a dict ready to be passed directly to the corresponding MCP tool.

Decomposed from the monolithic lib/mcp_app_generator.py (3,073 lines) into
domain-grouped modules for maintainability.
"""

# Sprint
from .sprint import (
    generate_sprint_kanban,
    generate_sprint_timeline,
    generate_sprint_progress,
)

# Project / Infrastructure
from .project import (
    generate_project_circuit,
    generate_session_circuit,
    generate_module_dependency_map,
    generate_dependency_circuit,
    generate_workflow_health_circuit,
)

# Backlog
from .backlog import (
    generate_backlog_data_table,
    generate_memory_kanban,
    generate_tech_debt_kanban,
)

# Review
from .review import (
    generate_pr_review_rubric,
    generate_retrospective,
    generate_pr_impact_rubric,
    generate_review_checklist_mcp,
)

# Analytics
from .analytics import (
    generate_session_analytics,
    generate_health_report,
    generate_progress_tracker,
    generate_contributor_data_table,
    generate_decision_health_circuit,
)

# Knowledge
from .knowledge import (
    generate_knowledge_concept_map,
    generate_gap_visualiser,
    generate_idea_canvas,
    generate_complexity_concept_map,
    generate_collaboration_map,
    generate_doc_coverage_gap,
)

# Digest
from .digest import (
    generate_digest,
    generate_save_digest,
    generate_code_story_digest,
    generate_changelog_timeline,
    generate_release_digest,
    generate_weather_digest,
    generate_focus_digest,
    generate_velocity_dashboard,
)

# Wellbeing
from .wellbeing import (
    generate_mood_timeline,
    generate_achievement_progress,
    generate_focus_cards_board,
    generate_routine_timeline,
    generate_scorecard_rubric,
    generate_session_achievements_progress,
)

# Tools
from .tools import (
    generate_smart_commit_options,
    generate_threat_matrix,
    generate_pair_suggestions_table,
)

# CLI
from .cli import main

__all__ = [
    # Sprint
    "generate_sprint_kanban",
    "generate_sprint_timeline",
    "generate_sprint_progress",
    # Project
    "generate_project_circuit",
    "generate_session_circuit",
    "generate_module_dependency_map",
    "generate_dependency_circuit",
    "generate_workflow_health_circuit",
    # Backlog
    "generate_backlog_data_table",
    "generate_memory_kanban",
    "generate_tech_debt_kanban",
    # Review
    "generate_pr_review_rubric",
    "generate_retrospective",
    "generate_pr_impact_rubric",
    "generate_review_checklist_mcp",
    # Analytics
    "generate_session_analytics",
    "generate_health_report",
    "generate_progress_tracker",
    "generate_contributor_data_table",
    "generate_decision_health_circuit",
    # Knowledge
    "generate_knowledge_concept_map",
    "generate_gap_visualiser",
    "generate_idea_canvas",
    "generate_complexity_concept_map",
    "generate_collaboration_map",
    "generate_doc_coverage_gap",
    # Digest
    "generate_digest",
    "generate_save_digest",
    "generate_code_story_digest",
    "generate_changelog_timeline",
    "generate_release_digest",
    "generate_weather_digest",
    "generate_focus_digest",
    "generate_velocity_dashboard",
    # Wellbeing
    "generate_mood_timeline",
    "generate_achievement_progress",
    "generate_focus_cards_board",
    "generate_routine_timeline",
    "generate_scorecard_rubric",
    "generate_session_achievements_progress",
    # Tools
    "generate_smart_commit_options",
    "generate_threat_matrix",
    "generate_pair_suggestions_table",
    # CLI
    "main",
]
