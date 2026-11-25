#!/usr/bin/env python3
"""
Copilot "What's Up?" Status Generator

Generates a comprehensive status report combining:
- GRIP RSI loop status (improvements, tests, patterns)
- GitHub PRs and issues
- Copilot ritual schedule
- Recent git activity
- Session state

Output formats: slack (mrkdwn), text (CLI), json

Usage:
    python3 aide-whatsup.py [--format slack|text|json]
    python3 aide-whatsup.py --send-slack  # Generate and send to Slack DM

Version: 1.0.0
"""

import json
import os
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", str(Path.home() / ".claude")))

sys.path.insert(0, str(CLAUDE_DIR / "lib"))


def get_git_status() -> dict:
    """Get recent git activity from GRIP repo."""
    try:
        # Recent commits (last 5)
        result = subprocess.run(
            ["git", "-C", str(CLAUDE_DIR), "log", "--oneline", "-5"],
            capture_output=True, text=True, timeout=10,
        )
        commits = result.stdout.strip().split("\n") if result.stdout.strip() else []

        # Current branch
        result = subprocess.run(
            ["git", "-C", str(CLAUDE_DIR), "branch", "--show-current"],
            capture_output=True, text=True, timeout=10,
        )
        branch = result.stdout.strip()

        # Uncommitted changes
        result = subprocess.run(
            ["git", "-C", str(CLAUDE_DIR), "status", "--porcelain"],
            capture_output=True, text=True, timeout=10,
        )
        changes = len(result.stdout.strip().split("\n")) if result.stdout.strip() else 0

        return {
            "branch": branch,
            "recent_commits": commits[:5],
            "uncommitted_changes": changes,
        }
    except Exception as e:
        return {"error": str(e)}


def get_rsi_status() -> dict:
    """Get RSI loop metrics."""
    metrics = {"improvements": 0, "tests": 0, "patterns_ready": 0, "insights": 0}

    # Improvement log count
    imp_log = CLAUDE_DIR / "improvement-log.jsonl"
    if imp_log.exists():
        with open(imp_log) as f:
            metrics["improvements"] = sum(1 for _ in f)

    # Test count (from last run)
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pytest", str(CLAUDE_DIR / "tests"), "-q", "--tb=no", "--co"],
            capture_output=True, text=True, timeout=30,
            cwd=str(CLAUDE_DIR),
        )
        # Count lines that look like test items
        metrics["tests"] = sum(
            1 for line in result.stdout.split("\n")
            if "::" in line and "test_" in line
        )
    except Exception:
        pass

    # Pattern counts
    pattern_file = CLAUDE_DIR / "pattern-counts.json"
    if pattern_file.exists():
        try:
            data = json.loads(pattern_file.read_text())
            ready = sum(
                1 for name, info in data.items()
                if name != "_meta" and isinstance(info, dict)
                and info.get("count", 0) >= 3 and not info.get("skill_created")
            )
            metrics["patterns_ready"] = ready
        except Exception:
            pass

    # Insights count
    insights_file = CLAUDE_DIR / "learning" / "insights.jsonl"
    if insights_file.exists():
        with open(insights_file) as f:
            metrics["insights"] = sum(1 for _ in f)

    return metrics


def get_github_status() -> dict:
    """Get GitHub PRs and issues."""
    repo = "grip-platform/GRIP"
    result = {"prs": [], "issues": [], "repo": repo}

    try:
        # Open PRs
        pr_result = subprocess.run(
            ["gh", "pr", "list", "-R", repo, "--json", "number,title,state,updatedAt"],
            capture_output=True, text=True, timeout=15,
        )
        if pr_result.returncode == 0 and pr_result.stdout.strip():
            try:
                result["prs"] = json.loads(pr_result.stdout)
            except json.JSONDecodeError:
                result["prs"] = []

        # Open issues
        issue_result = subprocess.run(
            ["gh", "issue", "list", "-R", repo, "--json", "number,title,state,labels"],
            capture_output=True, text=True, timeout=15,
        )
        if issue_result.returncode == 0 and issue_result.stdout.strip():
            try:
                result["issues"] = json.loads(issue_result.stdout)
            except json.JSONDecodeError:
                result["issues"] = []

    except Exception as e:
        result["error"] = str(e)

    return result


def get_copilot_status() -> dict:
    """Get copilot ritual and decision status."""
    try:
        from aide.core import CopilotCore
        copilot = CopilotCore()
        return copilot.handle("status")
    except Exception as e:
        return {"error": str(e)}


def format_slack(data: dict) -> str:
    """Format as Slack mrkdwn."""
    now = datetime.now(timezone.utc)
    content_hash = data.get("content_hash", "?")[:8]
    lines = [f":robot_face: *GRIP Copilot Status* | {now.strftime('%Y-%m-%d %H:%M UTC')} | `{content_hash}`"]
    lines.append("")

    # Git
    git = data.get("git", {})
    if git and not git.get("error"):
        lines.append(f":git: *Git*: `{git.get('branch', 'unknown')}` | {git.get('uncommitted_changes', 0)} uncommitted changes")
        recent = git.get("recent_commits", [])
        if recent:
            lines.append(f"  Latest: `{recent[0]}`")

    # RSI
    rsi = data.get("rsi", {})
    if rsi:
        lines.append("")
        lines.append(":chart_with_upwards_trend: *RSI Loop*")
        lines.append(f"  {rsi.get('improvements', 0)} improvements | {rsi.get('tests', 0)} tests | {rsi.get('insights', 0)} insights")
        if rsi.get("patterns_ready", 0) > 0:
            lines.append(f"  :sparkles: {rsi['patterns_ready']} patterns ready for skill generation (YSH 3x)")

    # GitHub
    gh = data.get("github", {})
    if gh and not gh.get("error"):
        lines.append("")
        lines.append(f":octocat: *GitHub* ({gh.get('repo', 'GRIP')})")
        lines.append(f"  {len(gh.get('prs', []))} open PRs | {len(gh.get('issues', []))} open issues")
        for issue in gh.get("issues", [])[:3]:
            lines.append(f"  #{issue.get('number', '?')} {issue.get('title', 'Untitled')}")

    # Copilot
    copilot = data.get("copilot", {})
    if copilot and not copilot.get("error"):
        lines.append("")
        lines.append(f":briefcase: *Copilot* ({copilot.get('company', 'N/A')})")
        enabled_rituals = [r for r in copilot.get("rituals", []) if r.get("enabled")]
        due_rituals = [r for r in enabled_rituals if r.get("is_due")]
        lines.append(f"  {len(enabled_rituals)} rituals active | {len(due_rituals)} due now")
        decisions = copilot.get("decisions", {})
        if decisions:
            lines.append(f"  {decisions.get('decisions', 0)} decisions logged | {decisions.get('resolution_rate', 0):.0%} resolved")

        integrations = copilot.get("integrations", {})
        active = sum(1 for v in integrations.values() if v)
        lines.append(f"  Integrations: {active}/{len(integrations)} active ({', '.join(k for k, v in integrations.items() if v)})")

    return "\n".join(lines)


def format_slack_blocks(data: dict) -> list:
    """Format as Slack Block Kit blocks."""
    try:
        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
        from aide.integrations import text_to_blocks
        return text_to_blocks(format_slack(data))
    except (ImportError, Exception):
        return [{"type": "section", "text": {"type": "mrkdwn", "text": format_slack(data)}}]


def format_text(data: dict) -> str:
    """Format as plain text for CLI."""
    # Reuse Slack format but strip mrkdwn
    slack = format_slack(data)
    # Remove emoji codes
    import re
    text = re.sub(r":[a-z_]+:", "", slack)
    text = text.replace("*", "")
    return text


def generate_report() -> dict:
    """Generate the full status report with content fingerprint."""
    import hashlib

    report = {
        "git": get_git_status(),
        "rsi": get_rsi_status(),
        "github": get_github_status(),
        "copilot": get_copilot_status(),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }

    # Content fingerprint: hash of data-bearing fields (excludes timestamp)
    # Lets users see when actual data changed vs just timestamp refresh
    content_key = json.dumps({
        "branch": report["git"].get("branch", ""),
        "commits": report["git"].get("recent_commits", [])[:3],
        "changes": report["git"].get("uncommitted_changes", 0),
        "prs": len(report["github"].get("prs", [])),
        "issues": len(report["github"].get("issues", [])),
        "improvements": report.get("rsi", {}).get("improvements_count", 0),
        "tests": report.get("rsi", {}).get("test_count", 0),
        "rituals": len(report.get("copilot", {}).get("active_rituals", [])),
    }, sort_keys=True)
    report["content_hash"] = hashlib.sha256(content_key.encode()).hexdigest()[:8]

    return report


def main():
    import argparse

    parser = argparse.ArgumentParser(description="GRIP Copilot What's Up?")
    parser.add_argument("--format", choices=["slack", "text", "json"], default="text")
    parser.add_argument("--send-slack", action="store_true", help="Send report as Slack DM to founder")
    parser.add_argument("--slack-channel", default="", help="Post to specific Slack channel")
    args = parser.parse_args()

    report = generate_report()

    if args.send_slack or args.slack_channel:
        from aide.core import CopilotCore
        from aide.thread_registry import CopilotThreadRegistry
        copilot = CopilotCore()
        message = format_slack(report)

        if args.slack_channel:
            result = copilot.hub.slack.post_channel(args.slack_channel, message)
            success = result.get("ok", False)
            channel_id = args.slack_channel
        else:
            # Send DM to founder
            team = getattr(copilot.company, "team", []) if hasattr(copilot, "company") else []
            founder = next(
                (m for m in team if getattr(m, "role", None) == "founder"), None
            )
            if founder and getattr(founder, "slack_id", None):
                result = copilot.hub.slack.send_dm(founder.slack_id, message)
                success = result.get("ok", False)
                channel_id = result.get("channel", "")
            else:
                print("ERROR: No founder with Slack ID configured", file=sys.stderr)
                sys.exit(1)

        # Register thread for reply continuity
        if success and result.get("ts") and channel_id:
            CopilotThreadRegistry.register(
                channel_id=channel_id,
                thread_ts=result["ts"],
                origin="briefing",
                ritual_id="whatsup",
                context={"report_type": "status"},
            )

        if success:
            print(f"Status report sent to Slack (thread: {result.get('ts', 'unknown')})")
        else:
            print(f"Failed to send to Slack: {result.get('error', 'unknown')}", file=sys.stderr)
            sys.exit(1)
        return

    if args.format == "json":
        print(json.dumps(report, indent=2, default=str))
    elif args.format == "slack":
        print(format_slack(report))
    else:
        print(format_text(report))


if __name__ == "__main__":
    main()
