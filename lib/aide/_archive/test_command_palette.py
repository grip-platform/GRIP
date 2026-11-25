"""Tests for the copilot command palette."""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "lib"))
sys.path.insert(0, str(Path(__file__).parent.parent / "lib" / "copilot"))
from aide.command_palette import (
    SuggestedAction,
    PaletteContext,
    rank_suggestions,
    format_suggestions_text,
    format_suggestions_slack,
    format_suggestions_json,
    ACTIONS,
)


# === SuggestedAction Tests ===

class TestSuggestedAction:

    def test_defaults(self):
        a = SuggestedAction("Test", "cmd", "desc")
        assert a.relevance == 0.0
        assert a.category == "general"
        assert a.reason == ""

    def test_custom_fields(self):
        a = SuggestedAction("Deploy", "vercel deploy", "Deploy to Vercel",
                           relevance=0.9, category="deploy", reason="Branch ready")
        assert a.relevance == 0.9
        assert a.category == "deploy"


# === PaletteContext Tests ===

class TestPaletteContext:

    def test_defaults(self):
        ctx = PaletteContext()
        assert ctx.hour == 0
        assert ctx.branch == ""
        assert ctx.has_uncommitted is False


# === Ranking Tests ===

class TestRankSuggestions:

    def _make_actions(self):
        return [
            SuggestedAction("Commit", "git commit", "Commit", category="git"),
            SuggestedAction("Sprint Report", "report", "Report", category="report"),
            SuggestedAction("Standup Notes", "standup", "Standup", category="report"),
            SuggestedAction("Create PR", "gh pr create", "PR", category="pr"),
            SuggestedAction("Run Tests", "pytest", "Tests", category="git"),
        ]

    def test_morning_boosts_standup(self):
        ctx = PaletteContext(hour=9)
        actions = self._make_actions()
        ranked = rank_suggestions(ctx, actions)
        standup = next(a for a in ranked if a.name == "Standup Notes")
        generic = next(a for a in ranked if a.name == "Run Tests")
        assert standup.relevance > generic.relevance

    def test_morning_boosts_sprint_report(self):
        ctx = PaletteContext(hour=9)
        actions = self._make_actions()
        ranked = rank_suggestions(ctx, actions)
        report = next(a for a in ranked if a.name == "Sprint Report")
        assert report.relevance >= 0.7

    def test_uncommitted_boosts_commit(self):
        ctx = PaletteContext(has_uncommitted=True)
        actions = [
            SuggestedAction("Commit Changes", "git commit", "Commit", category="git"),
            SuggestedAction("Deploy", "deploy", "Deploy", category="deploy"),
        ]
        ranked = rank_suggestions(ctx, actions)
        commit = next(a for a in ranked if a.name == "Commit Changes")
        assert commit.relevance >= 0.8
        assert "uncommitted" in commit.reason.lower()

    def test_staged_boosts_commit_higher(self):
        ctx = PaletteContext(has_staged=True)
        actions = [
            SuggestedAction("Commit Changes", "git commit", "Commit", category="git"),
            SuggestedAction("Deploy", "deploy", "Deploy", category="deploy"),
        ]
        ranked = rank_suggestions(ctx, actions)
        commit = next(a for a in ranked if a.name == "Commit Changes")
        assert commit.relevance >= 0.89

    def test_feature_branch_boosts_create_pr(self):
        ctx = PaletteContext(branch="feat/new-feature")
        actions = self._make_actions()
        ranked = rank_suggestions(ctx, actions)
        pr = next(a for a in ranked if a.name == "Create PR")
        assert pr.relevance >= 0.7
        assert "feat/new-feature" in pr.reason

    def test_main_branch_no_pr_boost(self):
        ctx = PaletteContext(branch="main")
        actions = self._make_actions()
        ranked = rank_suggestions(ctx, actions)
        pr = next(a for a in ranked if a.name == "Create PR")
        assert pr.relevance < 0.7

    def test_productive_day_boosts_reports(self):
        ctx = PaletteContext(recent_commits=8)
        actions = self._make_actions()
        ranked = rank_suggestions(ctx, actions)
        report = next(a for a in ranked if a.name == "Sprint Report")
        assert report.relevance >= 0.6
        assert "commits" in report.reason.lower()

    def test_no_commits_boosts_backlog(self):
        ctx = PaletteContext(recent_commits=0)
        actions = [
            SuggestedAction("Backlog Ranking", "rank", "Rank", category="report"),
            SuggestedAction("Commit", "commit", "Commit", category="git"),
        ]
        ranked = rank_suggestions(ctx, actions)
        backlog = next(a for a in ranked if a.name == "Backlog Ranking")
        assert backlog.relevance >= 0.6

    def test_friday_boosts_review(self):
        ctx = PaletteContext(day_of_week=4)  # Friday
        actions = [
            SuggestedAction("Sprint Report", "report", "Report", category="report"),
            SuggestedAction("Red Team Review", "redteam", "Review", category="copilot"),
            SuggestedAction("Commit", "commit", "Commit", category="git"),
        ]
        ranked = rank_suggestions(ctx, actions)
        review = next(a for a in ranked if a.name == "Red Team Review")
        assert review.relevance >= 0.6

    def test_relevance_capped_at_1(self):
        ctx = PaletteContext(
            hour=9, has_uncommitted=True, has_staged=True,
            branch="feat/x", recent_commits=10,
        )
        ranked = rank_suggestions(ctx)
        for a in ranked:
            assert a.relevance <= 1.0

    def test_sorted_descending(self):
        ctx = PaletteContext(has_uncommitted=True)
        ranked = rank_suggestions(ctx)
        for i in range(len(ranked) - 1):
            assert ranked[i].relevance >= ranked[i + 1].relevance

    def test_empty_actions(self):
        ctx = PaletteContext()
        ranked = rank_suggestions(ctx, [])
        assert ranked == []

    def test_end_of_day_boosts_reports(self):
        ctx = PaletteContext(hour=17)
        actions = [
            SuggestedAction("Deliverable Report", "report", "Report", category="report"),
            SuggestedAction("Branch Cleanup", "prune", "Cleanup", category="git"),
            SuggestedAction("Client Report", "client", "Client", category="report"),
            SuggestedAction("Run Tests", "pytest", "Tests", category="git"),
        ]
        ranked = rank_suggestions(ctx, actions)
        report = next(a for a in ranked if a.name == "Deliverable Report")
        assert report.relevance >= 0.7

    def test_work_hours_boost_git(self):
        ctx = PaletteContext(hour=14)
        actions = [
            SuggestedAction("Create PR", "pr", "PR", category="pr"),
            SuggestedAction("Standup Notes", "standup", "Standup", category="report"),
        ]
        ranked = rank_suggestions(ctx, actions)
        pr = next(a for a in ranked if a.name == "Create PR")
        assert pr.relevance >= 0.5


# === Text Formatter Tests ===

class TestFormatText:

    def test_header(self):
        suggestions = [SuggestedAction("Test", "cmd", "desc", relevance=0.8)]
        output = format_suggestions_text(suggestions)
        assert "Suggested Actions" in output
        assert "80%" in output

    def test_shows_command(self):
        suggestions = [SuggestedAction("Deploy", "vercel deploy", "Deploy", relevance=0.7)]
        output = format_suggestions_text(suggestions)
        assert "vercel deploy" in output

    def test_shows_reason(self):
        suggestions = [SuggestedAction("Test", "cmd", "desc", relevance=0.5, reason="Morning routine")]
        output = format_suggestions_text(suggestions)
        assert "Morning routine" in output

    def test_limit(self):
        suggestions = [SuggestedAction(f"Action {i}", f"cmd{i}", f"desc{i}") for i in range(20)]
        output = format_suggestions_text(suggestions, limit=5)
        assert "Action 4" in output
        assert "Action 5" not in output

    def test_empty_suggestions(self):
        output = format_suggestions_text([])
        assert "Suggested Actions" in output


# === Slack Formatter Tests ===

class TestFormatSlack:

    def test_uses_slack_bold(self):
        suggestions = [SuggestedAction("Test", "cmd", "desc", relevance=0.8)]
        output = format_suggestions_slack(suggestions)
        assert "*Suggested Actions*" in output
        assert "**" not in output

    def test_uses_italics_for_reason(self):
        suggestions = [SuggestedAction("Test", "cmd", "desc", relevance=0.8, reason="Good timing")]
        output = format_suggestions_slack(suggestions)
        assert "_Good timing_" in output

    def test_uses_code_for_command(self):
        suggestions = [SuggestedAction("Test", "pytest tests/", "desc", relevance=0.8)]
        output = format_suggestions_slack(suggestions)
        assert "`pytest tests/`" in output

    def test_filters_low_relevance(self):
        suggestions = [
            SuggestedAction("High", "cmd1", "d", relevance=0.8),
            SuggestedAction("Low", "cmd2", "d", relevance=0.1),
        ]
        output = format_suggestions_slack(suggestions)
        assert "High" in output
        assert "Low" not in output


# === JSON Formatter Tests ===

class TestFormatJson:

    def test_valid_json(self):
        suggestions = [SuggestedAction("Test", "cmd", "desc", relevance=0.8, category="git")]
        output = format_suggestions_json(suggestions)
        data = json.loads(output)
        assert "suggestions" in data
        assert len(data["suggestions"]) == 1
        assert data["suggestions"][0]["name"] == "Test"

    def test_includes_all_fields(self):
        suggestions = [SuggestedAction("Deploy", "cmd", "desc", relevance=0.9,
                                       category="deploy", reason="Branch ready")]
        data = json.loads(format_suggestions_json(suggestions))
        s = data["suggestions"][0]
        assert s["name"] == "Deploy"
        assert s["relevance"] == 0.9
        assert s["category"] == "deploy"
        assert s["reason"] == "Branch ready"

    def test_limit(self):
        suggestions = [SuggestedAction(f"A{i}", "cmd", "d") for i in range(20)]
        data = json.loads(format_suggestions_json(suggestions, limit=5))
        assert len(data["suggestions"]) == 5

    def test_empty(self):
        data = json.loads(format_suggestions_json([]))
        assert data["suggestions"] == []


# === Registry Tests ===

class TestActionRegistry:

    def test_all_actions_have_required_fields(self):
        for action in ACTIONS:
            assert action.name
            assert action.command
            assert action.description
            assert action.category in ("general", "git", "pr", "deploy", "report", "copilot")

    def test_no_duplicate_names(self):
        names = [a.name for a in ACTIONS]
        assert len(names) == len(set(names))

    def test_minimum_action_count(self):
        assert len(ACTIONS) >= 15
