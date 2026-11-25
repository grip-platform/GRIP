"""Tests for the client delivery report generator (Issue #148)."""

import json
import os
import sys
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT / "lib"))
sys.path.insert(0, str(REPO_ROOT / "lib" / "copilot"))

from aide.delivery_report import (
    PREntry,
    IssueEntry,
    DeliveryReport,
    is_internal,
    classify_pr_title,
    make_client_safe,
    get_merged_prs,
    get_closed_issues,
    get_commit_count,
    generate_report,
    format_slack,
    format_slack_blocks,
    format_markdown,
    format_json,
    markdown_to_mrkdwn,
)


class TestIsInternal(unittest.TestCase):
    """Test internal content detection."""

    def test_rsi_detected(self):
        self.assertTrue(is_internal("feat: RSI loop improvements"))

    def test_goodhart_detected(self):
        self.assertTrue(is_internal("fix: goodhart guard threshold"))

    def test_broly_detected(self):
        self.assertTrue(is_internal("chore: broly MESH configuration"))

    def test_ralph_detected(self):
        self.assertTrue(is_internal("fix: Ralph review timeout"))

    def test_context_gate_detected(self):
        self.assertTrue(is_internal("feat: add context-gate hook"))

    def test_normal_feature_not_internal(self):
        self.assertFalse(is_internal("feat: add user authentication"))

    def test_normal_fix_not_internal(self):
        self.assertFalse(is_internal("fix: login button alignment"))

    def test_empty_string(self):
        self.assertFalse(is_internal(""))


class TestClassifyPrTitle(unittest.TestCase):
    """Test PR title classification."""

    def test_feature(self):
        self.assertEqual(classify_pr_title("feat: add auth"), "feature")

    def test_fix(self):
        self.assertEqual(classify_pr_title("fix: login bug"), "fix")

    def test_chore(self):
        self.assertEqual(classify_pr_title("chore: cleanup"), "chore")

    def test_refactor(self):
        self.assertEqual(classify_pr_title("refactor: auth module"), "chore")

    def test_docs(self):
        self.assertEqual(classify_pr_title("docs: update readme"), "chore")

    def test_unknown_defaults_chore(self):
        self.assertEqual(classify_pr_title("something random"), "chore")


class TestMakeClientSafe(unittest.TestCase):
    """Test client-safe title transformation."""

    def test_strip_feat_prefix(self):
        result = make_client_safe("feat: add user authentication")
        self.assertEqual(result, "Add user authentication")

    def test_strip_fix_prefix(self):
        result = make_client_safe("fix: login button alignment")
        self.assertEqual(result, "Login button alignment")

    def test_strip_scoped_prefix(self):
        result = make_client_safe("feat(auth): add OAuth2 support")
        self.assertEqual(result, "Add OAuth2 support")

    def test_no_prefix(self):
        result = make_client_safe("Add user dashboard")
        self.assertEqual(result, "Add user dashboard")

    def test_capitalize_first(self):
        result = make_client_safe("fix: minor alignment issue")
        self.assertEqual(result, "Minor alignment issue")

    def test_empty_string(self):
        result = make_client_safe("")
        self.assertEqual(result, "")


class TestGetMergedPrs(unittest.TestCase):
    """Test PR fetching with date filtering."""

    @patch("subprocess.run")
    def test_filters_by_date_range(self, mock_run):
        now = datetime.now(timezone.utc)
        in_range = (now - timedelta(days=3)).isoformat()
        out_of_range = (now - timedelta(days=30)).isoformat()

        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps([
                {"number": 1, "title": "feat: new feature", "mergedAt": in_range},
                {"number": 2, "title": "fix: old fix", "mergedAt": out_of_range},
            ]),
        )

        since = (now - timedelta(days=7)).isoformat()
        until = now.isoformat()
        prs = get_merged_prs(since, until)

        self.assertEqual(len(prs), 1)
        self.assertEqual(prs[0].number, 1)

    @patch("subprocess.run")
    def test_classifies_categories(self, mock_run):
        now = datetime.now(timezone.utc).isoformat()
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps([
                {"number": 1, "title": "feat: auth", "mergedAt": now},
                {"number": 2, "title": "fix: bug", "mergedAt": now},
            ]),
        )

        since = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        prs = get_merged_prs(since, now)

        self.assertEqual(prs[0].category, "feature")
        self.assertEqual(prs[1].category, "fix")

    @patch("subprocess.run")
    def test_handles_timeout(self, mock_run):
        import subprocess
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="gh", timeout=15)
        prs = get_merged_prs("2026-01-01T00:00:00+00:00", "2026-02-01T00:00:00+00:00")
        self.assertEqual(prs, [])


class TestGetClosedIssues(unittest.TestCase):
    """Test issue fetching."""

    @patch("subprocess.run")
    def test_filters_by_date(self, mock_run):
        now = datetime.now(timezone.utc)
        in_range = (now - timedelta(days=2)).isoformat()

        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps([
                {"number": 10, "title": "Bug report", "closedAt": in_range, "labels": [{"name": "bug"}]},
            ]),
        )

        since = (now - timedelta(days=7)).isoformat()
        issues = get_closed_issues(since, now.isoformat())

        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].labels, ["bug"])


class TestGetCommitCount(unittest.TestCase):
    """Test commit counting."""

    @patch("subprocess.run")
    def test_counts_lines(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="abc1234 first commit\ndef5678 second commit\n",
        )
        count = get_commit_count("2026-01-01", "2026-02-01")
        self.assertEqual(count, 2)

    @patch("subprocess.run")
    def test_empty_output(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="")
        count = get_commit_count("2026-01-01", "2026-02-01")
        self.assertEqual(count, 0)


class TestGenerateReport(unittest.TestCase):
    """Test full report generation."""

    @patch("copilot.delivery_report.get_open_issues_for_next_sprint")
    @patch("copilot.delivery_report.get_commit_count")
    @patch("copilot.delivery_report.get_closed_issues")
    @patch("copilot.delivery_report.get_merged_prs")
    def test_separates_categories(self, mock_prs, mock_issues, mock_commits, mock_next):
        mock_prs.return_value = [
            PREntry(number=1, title="feat: add auth", merged_at="", category="feature", client_title="Add auth"),
            PREntry(number=2, title="fix: login bug", merged_at="", category="fix", client_title="Login bug"),
            PREntry(number=3, title="chore: RSI loop fix", merged_at="", category="chore", client_title="RSI loop fix"),
        ]
        mock_issues.return_value = [IssueEntry(number=10, title="Bug", closed_at="")]
        mock_commits.return_value = 15
        mock_next.return_value = []

        report = generate_report("2026-02-01T00:00:00+00:00", "2026-02-16T00:00:00+00:00")

        self.assertEqual(len(report.features), 1)
        self.assertEqual(len(report.fixes), 1)
        # RSI is internal -> maintenance
        self.assertEqual(len(report.maintenance), 1)
        self.assertEqual(report.prs_merged, 3)
        self.assertEqual(report.commits_total, 15)

    @patch("copilot.delivery_report.get_open_issues_for_next_sprint")
    @patch("copilot.delivery_report.get_commit_count")
    @patch("copilot.delivery_report.get_closed_issues")
    @patch("copilot.delivery_report.get_merged_prs")
    def test_internal_prs_go_to_maintenance(self, mock_prs, mock_issues, mock_commits, mock_next):
        mock_prs.return_value = [
            PREntry(number=1, title="feat: Broly MESH config", merged_at="", category="feature", client_title="Broly MESH config"),
        ]
        mock_issues.return_value = []
        mock_commits.return_value = 0
        mock_next.return_value = []

        report = generate_report("2026-02-01T00:00:00+00:00", "2026-02-16T00:00:00+00:00")

        self.assertEqual(len(report.features), 0)
        self.assertEqual(len(report.maintenance), 1)


class TestFormatSlack(unittest.TestCase):
    """Test Slack formatting."""

    def test_features_shown(self):
        report = DeliveryReport(
            period_start="2026-02-01T00:00:00Z",
            period_end="2026-02-16T00:00:00Z",
            features=[{"number": 1, "title": "Add auth"}],
            prs_merged=1, commits_total=5,
        )
        output = format_slack(report)
        self.assertIn("Features Shipped", output)
        self.assertIn("Add auth", output)
        self.assertIn("PR #1", output)

    def test_fixes_shown(self):
        report = DeliveryReport(
            period_start="2026-02-01T00:00:00Z",
            period_end="2026-02-16T00:00:00Z",
            fixes=[{"number": 2, "title": "Login fix"}],
        )
        output = format_slack(report)
        self.assertIn("Bug Fixes", output)

    def test_maintenance_count_only(self):
        report = DeliveryReport(
            period_start="2026-02-01T00:00:00Z",
            period_end="2026-02-16T00:00:00Z",
            maintenance=[{"number": 3, "title": "Internal"}, {"number": 4, "title": "Also internal"}],
        )
        output = format_slack(report)
        self.assertIn("2 infrastructure", output)
        # Should NOT list individual maintenance items
        self.assertNotIn("Internal", output)

    def test_next_sprint_shown(self):
        report = DeliveryReport(
            period_start="2026-02-01T00:00:00Z",
            period_end="2026-02-16T00:00:00Z",
            next_sprint=[{"number": 5, "title": "New feature"}],
        )
        output = format_slack(report)
        self.assertIn("Next Sprint", output)


class TestFormatMarkdown(unittest.TestCase):
    """Test Markdown formatting."""

    def test_has_title(self):
        report = DeliveryReport(
            period_start="2026-02-01T00:00:00Z",
            period_end="2026-02-16T00:00:00Z",
        )
        output = format_markdown(report)
        self.assertIn("# Delivery Report", output)

    def test_features_section(self):
        report = DeliveryReport(
            period_start="2026-02-01T00:00:00Z",
            period_end="2026-02-16T00:00:00Z",
            features=[{"number": 1, "title": "Auth system"}],
        )
        output = format_markdown(report)
        self.assertIn("## Features Shipped", output)
        self.assertIn("**PR #1**", output)


class TestFormatJson(unittest.TestCase):
    """Test JSON output."""

    def test_valid_json(self):
        report = DeliveryReport(
            period_start="2026-02-01", period_end="2026-02-16",
            generated_at="2026-02-16T08:00:00Z",
        )
        output = format_json(report)
        parsed = json.loads(output)
        self.assertEqual(parsed["period_start"], "2026-02-01")
        self.assertIn("features", parsed)




class TestMarkdownToMrkdwn(unittest.TestCase):
    """Test GitHub markdown to Slack mrkdwn conversion."""

    def test_bold_conversion(self):
        self.assertEqual(markdown_to_mrkdwn("**hello**"), "*hello*")

    def test_double_bold(self):
        result = markdown_to_mrkdwn("**first** and **second**")
        self.assertEqual(result, "*first* and *second*")

    def test_link_conversion(self):
        result = markdown_to_mrkdwn("[click here](https://example.com)")
        self.assertEqual(result, "<https://example.com|click here>")

    def test_header_conversion(self):
        result = markdown_to_mrkdwn("## Features Shipped")
        self.assertEqual(result, "*Features Shipped*")

    def test_mixed_content(self):
        text = "## Report\n**3 PRs** merged. See [details](https://url)."
        result = markdown_to_mrkdwn(text)
        self.assertIn("*Report*", result)
        self.assertIn("*3 PRs*", result)
        self.assertIn("<https://url|details>", result)
        self.assertNotIn("**", result)
        self.assertNotIn("[", result)

    def test_already_mrkdwn_unchanged(self):
        text = "*already bold* and _italic_"
        result = markdown_to_mrkdwn(text)
        self.assertIn("*already bold*", result)

    def test_plain_text_unchanged(self):
        self.assertEqual(markdown_to_mrkdwn("just text"), "just text")

    def test_empty_string(self):
        self.assertEqual(markdown_to_mrkdwn(""), "")

    def test_special_chars_preserved(self):
        text = "Price is $100 & tax <5%"
        result = markdown_to_mrkdwn(text)
        self.assertIn("$100", result)
        self.assertIn("&", result)

    def test_nested_bold_in_link(self):
        text = "[**bold link**](https://example.com)"
        result = markdown_to_mrkdwn(text)
        self.assertNotIn("**", result)
        self.assertIn("example.com", result)

    def test_multiline_headers(self):
        text = "# Title\n## Subtitle\n### Deep"
        result = markdown_to_mrkdwn(text)
        self.assertIn("*Title*", result)
        self.assertIn("*Subtitle*", result)
        self.assertIn("*Deep*", result)
        self.assertNotIn("#", result)


class TestFormatSlackBlocks(unittest.TestCase):
    """Test Block Kit block generation."""

    def test_returns_list(self):
        report = DeliveryReport(
            period_start="2026-02-01T00:00:00Z",
            period_end="2026-02-16T00:00:00Z",
        )
        blocks = format_slack_blocks(report)
        self.assertIsInstance(blocks, list)

    def test_has_header(self):
        report = DeliveryReport(
            period_start="2026-02-01T00:00:00Z",
            period_end="2026-02-16T00:00:00Z",
        )
        blocks = format_slack_blocks(report)
        header = blocks[0]
        self.assertEqual(header["type"], "header")
        self.assertIn("2026-02-01", header["text"]["text"])

    def test_summary_fields(self):
        report = DeliveryReport(
            period_start="2026-02-01T00:00:00Z",
            period_end="2026-02-16T00:00:00Z",
            prs_merged=5,
            issues_closed=3,
            commits_total=42,
        )
        blocks = format_slack_blocks(report)
        summary = blocks[1]
        self.assertEqual(summary["type"], "section")
        self.assertEqual(len(summary["fields"]), 4)

    def test_features_section_present(self):
        report = DeliveryReport(
            period_start="2026-02-01T00:00:00Z",
            period_end="2026-02-16T00:00:00Z",
            features=[{"number": 1, "title": "Auth"}],
        )
        blocks = format_slack_blocks(report)
        texts = [b.get("text", {}).get("text", "") for b in blocks if b.get("type") == "section"]
        has_features = any(":rocket:" in t for t in texts)
        self.assertTrue(has_features)

    def test_fixes_section_present(self):
        report = DeliveryReport(
            period_start="2026-02-01T00:00:00Z",
            period_end="2026-02-16T00:00:00Z",
            fixes=[{"number": 2, "title": "Login bug"}],
        )
        blocks = format_slack_blocks(report)
        texts = [b.get("text", {}).get("text", "") for b in blocks if b.get("type") == "section"]
        has_fixes = any(":wrench:" in t for t in texts)
        self.assertTrue(has_fixes)

    def test_maintenance_context(self):
        report = DeliveryReport(
            period_start="2026-02-01T00:00:00Z",
            period_end="2026-02-16T00:00:00Z",
            maintenance=[{"number": 3, "title": "Cleanup"}],
        )
        blocks = format_slack_blocks(report)
        context_blocks = [b for b in blocks if b.get("type") == "context"]
        self.assertTrue(len(context_blocks) >= 1)

    def test_next_sprint_present(self):
        report = DeliveryReport(
            period_start="2026-02-01T00:00:00Z",
            period_end="2026-02-16T00:00:00Z",
            next_sprint=[{"number": 10, "title": "Dashboard"}],
        )
        blocks = format_slack_blocks(report)
        texts = [b.get("text", {}).get("text", "") for b in blocks if b.get("type") == "section"]
        has_sprint = any(":calendar:" in t for t in texts)
        self.assertTrue(has_sprint)

    def test_empty_report_minimal_blocks(self):
        report = DeliveryReport(
            period_start="2026-02-01T00:00:00Z",
            period_end="2026-02-16T00:00:00Z",
        )
        blocks = format_slack_blocks(report)
        # Should have at least header + summary + context (timestamp)
        self.assertGreaterEqual(len(blocks), 3)

    def test_all_blocks_have_type(self):
        report = DeliveryReport(
            period_start="2026-02-01T00:00:00Z",
            period_end="2026-02-16T00:00:00Z",
            features=[{"number": 1, "title": "Auth"}],
            fixes=[{"number": 2, "title": "Bug"}],
            maintenance=[{"number": 3, "title": "Cleanup"}],
        )
        blocks = format_slack_blocks(report)
        for block in blocks:
            self.assertIn("type", block)

    def test_no_empty_text_fields(self):
        """Verify no Block Kit text fields are empty (Slack rejects these)."""
        report = DeliveryReport(
            period_start="2026-02-01T00:00:00Z",
            period_end="2026-02-16T00:00:00Z",
            features=[{"number": 1, "title": "Auth"}],
            fixes=[{"number": 2, "title": "Bug"}],
            maintenance=[{"number": 3, "title": "Infra"}],
            next_sprint=[{"number": 10, "title": "Dashboard"}],
        )
        blocks = format_slack_blocks(report)
        for block in blocks:
            if "text" in block and isinstance(block["text"], dict):
                self.assertTrue(block["text"].get("text"), f"Empty text in block: {block}")
            if "fields" in block:
                for field in block["fields"]:
                    self.assertTrue(field.get("text"), f"Empty field text: {field}")
            if "elements" in block:
                for elem in block["elements"]:
                    if "text" in elem:
                        self.assertTrue(elem["text"], f"Empty element text: {elem}")

    def test_blocks_serializable_as_json(self):
        """Block Kit blocks must be JSON-serializable for Slack API."""
        report = DeliveryReport(
            period_start="2026-02-01T00:00:00Z",
            period_end="2026-02-16T00:00:00Z",
            features=[{"number": 1, "title": "Auth"}],
        )
        blocks = format_slack_blocks(report)
        serialized = json.dumps(blocks)
        parsed = json.loads(serialized)
        self.assertEqual(len(parsed), len(blocks))


if __name__ == "__main__":
    unittest.main()
