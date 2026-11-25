"""Tests for the team standup bot (Issue #147).

Verifies team parsing, git activity fetching, cross-dependency detection,
and Slack formatting.
"""

import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT / "lib"))
sys.path.insert(0, str(REPO_ROOT / "lib" / "copilot"))

from aide.standup_bot import (
    TeamMember,
    MemberStandup,
    TeamStandup,
    parse_team_file,
    detect_cross_dependencies,
    detect_blockers,
    generate_member_standup,
    generate_team_standup,
    format_slack,
    format_json,
)


class TestParseTeamFile(unittest.TestCase):
    """Test parsing facts/team.md into TeamMember objects."""

    def test_parse_real_team_file(self):
        members = parse_team_file()
        self.assertGreater(len(members), 0)
        # Primary user should be first
        self.assertEqual(members[0].name, "Laurie")
        self.assertEqual(members[0].signature, "V>>")

    def test_parse_team_members(self):
        members = parse_team_file()
        names = [m.name for m in members]
        self.assertIn("Andre", names)
        self.assertIn("Fabio", names)
        self.assertIn("Arnold", names)

    def test_missing_team_file(self):
        with patch("copilot.standup_bot.TEAM_FILE", Path("/nonexistent/team.md")):
            members = parse_team_file()
        self.assertEqual(members, [])

    def test_team_member_fields(self):
        members = parse_team_file()
        laurie = members[0]
        self.assertEqual(laurie.mode, "direct")
        self.assertIn(">>", laurie.signature)


class TestDetectCrossDependencies(unittest.TestCase):
    """Test cross-dependency detection between team members."""

    def test_file_overlap_detected(self):
        standups = [
            MemberStandup(name="Alice", files_touched=["lib/auth.py", "lib/db.py"]),
            MemberStandup(name="Bob", files_touched=["lib/auth.py", "tests/test_auth.py"]),
        ]
        deps = detect_cross_dependencies(standups)
        self.assertEqual(len(deps), 1)
        self.assertEqual(deps[0]["type"], "file_overlap")
        self.assertIn("Alice", deps[0]["members"])
        self.assertIn("Bob", deps[0]["members"])
        self.assertIn("lib/auth.py", deps[0]["files"])

    def test_no_overlap(self):
        standups = [
            MemberStandup(name="Alice", files_touched=["lib/auth.py"]),
            MemberStandup(name="Bob", files_touched=["lib/db.py"]),
        ]
        deps = detect_cross_dependencies(standups)
        self.assertEqual(len(deps), 0)

    def test_three_way_overlap(self):
        standups = [
            MemberStandup(name="Alice", files_touched=["shared.py"]),
            MemberStandup(name="Bob", files_touched=["shared.py"]),
            MemberStandup(name="Charlie", files_touched=["shared.py"]),
        ]
        deps = detect_cross_dependencies(standups)
        # Should detect 3 pairs: A-B, A-C, B-C
        self.assertEqual(len(deps), 3)

    def test_empty_files(self):
        standups = [
            MemberStandup(name="Alice", files_touched=[]),
            MemberStandup(name="Bob", files_touched=[]),
        ]
        deps = detect_cross_dependencies(standups)
        self.assertEqual(len(deps), 0)


class TestDetectBlockers(unittest.TestCase):
    """Test blocker detection."""

    @patch("subprocess.run")
    def test_stale_pr_detected(self, mock_run):
        old_date = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps([{
                "number": 99,
                "title": "old PR",
                "createdAt": old_date,
            }]),
        )
        blockers = detect_blockers()
        self.assertEqual(len(blockers), 1)
        self.assertEqual(blockers[0]["type"], "stale_pr")
        self.assertGreaterEqual(blockers[0]["age_days"], 4)

    @patch("subprocess.run")
    def test_fresh_pr_not_blocked(self, mock_run):
        fresh_date = datetime.now(timezone.utc).isoformat()
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps([{
                "number": 100,
                "title": "fresh PR",
                "createdAt": fresh_date,
            }]),
        )
        blockers = detect_blockers()
        self.assertEqual(len(blockers), 0)

    @patch("subprocess.run")
    def test_subprocess_timeout_handled(self, mock_run):
        import subprocess
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="gh", timeout=15)
        blockers = detect_blockers()
        self.assertEqual(blockers, [])


class TestGenerateMemberStandup(unittest.TestCase):
    """Test individual member standup generation."""

    @patch("copilot.standup_bot.get_member_files")
    @patch("copilot.standup_bot.get_member_prs")
    @patch("copilot.standup_bot.get_member_commits")
    def test_standup_aggregation(self, mock_commits, mock_prs, mock_files):
        mock_commits.return_value = [
            {"hash": "abc12345", "message": "feat: add auth", "date": "2026-02-16"},
        ]
        mock_prs.return_value = {
            "authored": [{"number": 100, "title": "Auth feature"}],
            "reviewing": [],
        }
        mock_files.return_value = ["lib/auth.py"]

        member = TeamMember(name="Alice", signature="A>>", role="Dev", mode="direct")
        standup = generate_member_standup(member)

        self.assertEqual(standup.name, "Alice")
        self.assertEqual(len(standup.commits), 1)
        self.assertEqual(len(standup.prs_authored), 1)
        self.assertEqual(standup.files_touched, ["lib/auth.py"])


class TestGenerateTeamStandup(unittest.TestCase):
    """Test full team standup generation."""

    @patch("copilot.standup_bot.detect_blockers")
    @patch("copilot.standup_bot.detect_cross_dependencies")
    @patch("copilot.standup_bot.generate_member_standup")
    @patch("copilot.standup_bot.parse_team_file")
    def test_team_standup_structure(self, mock_parse, mock_gen, mock_deps, mock_block):
        mock_parse.return_value = [
            TeamMember(name="Alice", signature="A>>", role="Dev", mode="direct"),
        ]
        mock_gen.return_value = MemberStandup(
            name="Alice",
            commits=[{"hash": "abc", "message": "feat: auth", "date": ""}],
        )
        mock_deps.return_value = []
        mock_block.return_value = []

        result = generate_team_standup()
        self.assertIsNotNone(result.generated_at)
        self.assertEqual(len(result.members), 1)

    @patch("copilot.standup_bot.parse_team_file")
    def test_empty_team(self, mock_parse):
        mock_parse.return_value = []
        result = generate_team_standup()
        self.assertIn("No team members", result.summary)


class TestFormatSlack(unittest.TestCase):
    """Test Slack message formatting."""

    def test_active_member_formatted(self):
        standup = TeamStandup(
            generated_at="2026-02-16T08:15:00Z",
            members=[{
                "name": "Alice",
                "commits": [{"hash": "abc12345", "message": "feat: auth", "date": ""}],
                "prs_authored": [{"number": 100, "title": "Auth feature"}],
                "prs_reviewing": [],
                "files_touched": [],
                "active_branches": [],
            }],
        )
        output = format_slack(standup)
        self.assertIn("*Alice*", output)
        self.assertIn("feat: auth", output)
        self.assertIn("PR #100", output)

    def test_no_activity_message(self):
        standup = TeamStandup(
            generated_at="2026-02-16T08:15:00Z",
            members=[{
                "name": "Bob",
                "commits": [],
                "prs_authored": [],
                "prs_reviewing": [],
                "files_touched": [],
                "active_branches": [],
            }],
        )
        output = format_slack(standup)
        self.assertIn("No git activity", output)

    def test_cross_deps_shown(self):
        standup = TeamStandup(
            generated_at="2026-02-16T08:15:00Z",
            members=[{
                "name": "Alice",
                "commits": [{"hash": "abc", "message": "test", "date": ""}],
                "prs_authored": [], "prs_reviewing": [],
                "files_touched": [], "active_branches": [],
            }],
            cross_dependencies=[{
                "type": "file_overlap",
                "members": ["Alice", "Bob"],
                "files": ["shared.py"],
                "message": "Alice and Bob both modified: shared.py",
            }],
        )
        output = format_slack(standup)
        self.assertIn("Cross-Dependencies", output)
        self.assertIn("shared.py", output)

    def test_blockers_shown(self):
        standup = TeamStandup(
            generated_at="2026-02-16T08:15:00Z",
            members=[{
                "name": "Alice",
                "commits": [{"hash": "abc", "message": "test", "date": ""}],
                "prs_authored": [], "prs_reviewing": [],
                "files_touched": [], "active_branches": [],
            }],
            blockers=[{
                "type": "stale_pr",
                "pr": 50,
                "title": "Old PR",
                "age_days": 7,
            }],
        )
        output = format_slack(standup)
        self.assertIn("Blockers", output)
        self.assertIn("7 days", output)

    def test_json_output_valid(self):
        standup = TeamStandup(
            generated_at="2026-02-16T08:15:00Z",
            members=[],
        )
        output = format_json(standup)
        parsed = json.loads(output)
        self.assertEqual(parsed["generated_at"], "2026-02-16T08:15:00Z")


if __name__ == "__main__":
    unittest.main()
