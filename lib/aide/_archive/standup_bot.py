"""
Team Standup Bot — git-verified team standup generation.

Generates daily standup summaries per team member by cross-referencing
git commits, PR activity, and open issues. Detects cross-dependencies
and flags mismatches between team members' work streams.

Usage:
    python3 lib/copilot/standup_bot.py generate          # Full team standup
    python3 lib/copilot/standup_bot.py member <name>     # Single member
    python3 lib/copilot/standup_bot.py correlate         # Cross-dependency map
"""

import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", str(Path.home() / ".claude")))
TEAM_FILE = CLAUDE_DIR / "facts" / "team.md"


@dataclass
class TeamMember:
    """A team member parsed from facts/team.md."""
    name: str
    signature: str
    role: str
    mode: str
    git_authors: list = field(default_factory=list)

    @property
    def git_author_pattern(self) -> str:
        """Git author pattern for log filtering."""
        if self.git_authors:
            return "|".join(self.git_authors)
        return self.name


@dataclass
class MemberStandup:
    """Standup data for a single team member."""
    name: str
    commits: list = field(default_factory=list)
    prs_authored: list = field(default_factory=list)
    prs_reviewing: list = field(default_factory=list)
    files_touched: list = field(default_factory=list)
    active_branches: list = field(default_factory=list)


@dataclass
class TeamStandup:
    """Aggregated team standup with cross-dependencies."""
    generated_at: str = ""
    members: list = field(default_factory=list)
    cross_dependencies: list = field(default_factory=list)
    blockers: list = field(default_factory=list)
    summary: str = ""


def parse_team_file() -> list[TeamMember]:
    """Parse facts/team.md into TeamMember objects."""
    if not TEAM_FILE.exists():
        return []

    members = []
    content = TEAM_FILE.read_text()

    # Parse the Team Members table
    in_table = False
    for line in content.split("\n"):
        if "| Sig |" in line and "Name" in line:
            in_table = True
            continue
        if in_table and line.startswith("|---"):
            continue
        if in_table and line.startswith("|"):
            parts = [p.strip() for p in line.split("|")[1:-1]]
            if len(parts) >= 4:
                sig = parts[0].replace(">>", "").strip()
                name = parts[1].strip()
                role = parts[2].strip()
                mode = parts[3].strip()
                members.append(TeamMember(
                    name=name, signature=f"{sig}>>",
                    role=role, mode=mode,
                ))
        elif in_table and not line.strip().startswith("|"):
            in_table = False

    # Also parse primary user
    primary_match = re.search(r"\| Name \| (\w+) \|", content)
    primary_sig = re.search(r"\| Signature \| (\w+>>) \|", content)
    if primary_match:
        members.insert(0, TeamMember(
            name=primary_match.group(1),
            signature=primary_sig.group(1) if primary_sig else "V>>",
            role="Lead",
            mode="direct",
        ))

    return members


def get_member_commits(member: TeamMember, hours: int = 24) -> list[dict]:
    """Get git commits for a team member in the last N hours."""
    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    try:
        result = subprocess.run(
            ["git", "log", f"--since={since}",
             f"--author={member.git_author_pattern}",
             "--format=%H|%s|%ai", "--no-merges"],
            capture_output=True, text=True, timeout=10,
            cwd=str(CLAUDE_DIR),
        )
        if result.returncode != 0:
            return []
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return []

    commits = []
    for line in result.stdout.strip().split("\n"):
        if not line:
            continue
        parts = line.split("|", 2)
        if len(parts) >= 2:
            commits.append({
                "hash": parts[0][:8],
                "message": parts[1],
                "date": parts[2] if len(parts) > 2 else "",
            })
    return commits


def get_member_prs(member: TeamMember, limit: int = 5) -> dict:
    """Get PRs authored and reviewing for a team member."""
    authored = []
    reviewing = []

    try:
        result = subprocess.run(
            ["gh", "pr", "list", "-R", "grip-platform/GRIP",
             "--state", "open", "--limit", str(limit),
             "--json", "number,title,author,reviewRequests"],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode == 0 and result.stdout.strip():
            prs = json.loads(result.stdout)
            for pr in prs:
                author = pr.get("author", {}).get("login", "")
                if member.name.lower() in author.lower():
                    authored.append({
                        "number": pr["number"],
                        "title": pr["title"],
                    })
                # Check if member is requested reviewer
                for req in pr.get("reviewRequests", []):
                    reviewer = req.get("login", req.get("name", ""))
                    if member.name.lower() in reviewer.lower():
                        reviewing.append({
                            "number": pr["number"],
                            "title": pr["title"],
                        })
    except (subprocess.TimeoutExpired, FileNotFoundError, json.JSONDecodeError):
        pass

    return {"authored": authored, "reviewing": reviewing}


def get_member_files(member: TeamMember, hours: int = 24) -> list[str]:
    """Get files touched by a team member in the last N hours."""
    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    try:
        result = subprocess.run(
            ["git", "log", f"--since={since}",
             f"--author={member.git_author_pattern}",
             "--name-only", "--format=", "--no-merges"],
            capture_output=True, text=True, timeout=10,
            cwd=str(CLAUDE_DIR),
        )
        if result.returncode != 0:
            return []
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return []

    files = set()
    for line in result.stdout.strip().split("\n"):
        if line.strip():
            files.add(line.strip())
    return sorted(files)


def detect_cross_dependencies(standups: list[MemberStandup]) -> list[dict]:
    """Detect file overlap and PR blocking between team members."""
    deps = []

    # File overlap detection
    for i, a in enumerate(standups):
        for b in standups[i + 1:]:
            shared = set(a.files_touched) & set(b.files_touched)
            if shared:
                deps.append({
                    "type": "file_overlap",
                    "members": [a.name, b.name],
                    "files": sorted(shared)[:5],  # Top 5
                    "message": (
                        f"{a.name} and {b.name} both modified: "
                        f"{', '.join(sorted(shared)[:3])}"
                    ),
                })

    return deps


def detect_blockers() -> list[dict]:
    """Detect blocked PRs and failing tests."""
    blockers = []

    # Check for stale PRs (open > 3 days)
    try:
        result = subprocess.run(
            ["gh", "pr", "list", "-R", "grip-platform/GRIP",
             "--state", "open", "--json", "number,title,createdAt"],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode == 0 and result.stdout.strip():
            prs = json.loads(result.stdout)
            cutoff = datetime.now(timezone.utc) - timedelta(days=3)
            for pr in prs:
                created = pr.get("createdAt", "")
                if created:
                    try:
                        created_dt = datetime.fromisoformat(
                            created.replace("Z", "+00:00")
                        )
                        if created_dt < cutoff:
                            blockers.append({
                                "type": "stale_pr",
                                "pr": pr["number"],
                                "title": pr["title"],
                                "age_days": (
                                    datetime.now(timezone.utc) - created_dt
                                ).days,
                            })
                    except ValueError:
                        pass
    except (subprocess.TimeoutExpired, FileNotFoundError, json.JSONDecodeError):
        pass

    return blockers


def generate_member_standup(member: TeamMember, hours: int = 24) -> MemberStandup:
    """Generate standup data for a single team member."""
    commits = get_member_commits(member, hours)
    prs = get_member_prs(member)
    files = get_member_files(member, hours)

    return MemberStandup(
        name=member.name,
        commits=commits,
        prs_authored=prs["authored"],
        prs_reviewing=prs["reviewing"],
        files_touched=files,
    )


def generate_team_standup(hours: int = 24) -> TeamStandup:
    """Generate full team standup with cross-dependencies."""
    members = parse_team_file()
    if not members:
        return TeamStandup(
            generated_at=datetime.now(timezone.utc).isoformat(),
            summary="No team members found in facts/team.md.",
        )

    standups = []
    for member in members:
        standup = generate_member_standup(member, hours)
        standups.append(standup)

    cross_deps = detect_cross_dependencies(standups)
    blockers = detect_blockers()

    return TeamStandup(
        generated_at=datetime.now(timezone.utc).isoformat(),
        members=[asdict(s) for s in standups],
        cross_dependencies=cross_deps,
        blockers=blockers,
    )


def format_slack(standup: TeamStandup) -> str:
    """Format team standup for Slack delivery."""
    lines = [f":clipboard: *Team Standup* | {standup.generated_at[:16]}Z"]
    lines.append("")

    active_members = [
        m for m in standup.members
        if m.get("commits") or m.get("prs_authored") or m.get("prs_reviewing")
    ]

    if not active_members:
        lines.append("_No git activity detected in the last 24 hours._")
        return "\n".join(lines)

    for member in active_members:
        name = member["name"]
        commits = member.get("commits", [])
        prs_authored = member.get("prs_authored", [])
        prs_reviewing = member.get("prs_reviewing", [])

        lines.append(f"*{name}*")

        if commits:
            lines.append("  _Yesterday:_")
            for c in commits[:3]:
                lines.append(f"  - `{c['hash']}` {c['message']}")
            if len(commits) > 3:
                lines.append(f"  _...and {len(commits) - 3} more commits_")

        if prs_authored:
            lines.append("  _Open PRs:_")
            for pr in prs_authored:
                lines.append(f"  - PR #{pr['number']}: {pr['title']}")

        if prs_reviewing:
            lines.append("  _Reviewing:_")
            for pr in prs_reviewing:
                lines.append(f"  - PR #{pr['number']}: {pr['title']}")

        if not commits and not prs_authored:
            lines.append("  _No git activity in last 24h_")

        lines.append("")

    # Cross-dependencies
    if standup.cross_dependencies:
        lines.append(":warning: *Cross-Dependencies*")
        for dep in standup.cross_dependencies:
            lines.append(f"  - {dep['message']}")
        lines.append("")

    # Blockers
    if standup.blockers:
        lines.append(":rotating_light: *Blockers*")
        for b in standup.blockers:
            if b["type"] == "stale_pr":
                lines.append(
                    f"  - PR #{b['pr']} ({b['title']}) open for {b['age_days']} days"
                )
        lines.append("")

    return "\n".join(lines)


def format_slack_blocks(standup: TeamStandup) -> list:
    """Format team standup as Slack Block Kit blocks."""
    try:
        from .integrations import text_to_blocks
        return text_to_blocks(format_slack(standup))
    except (ImportError, Exception):
        return [{"type": "section", "text": {"type": "mrkdwn", "text": format_slack(standup)}}]


def format_json(standup: TeamStandup) -> str:
    """Format team standup as JSON."""
    return json.dumps(asdict(standup), indent=2, default=str)


def main():
    """CLI entry point."""
    if len(sys.argv) < 2:
        print("Usage: standup_bot.py <generate|member|correlate>")
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "generate":
        hours = int(sys.argv[2]) if len(sys.argv) > 2 else 24
        standup = generate_team_standup(hours)
        output = sys.argv[3] if len(sys.argv) > 3 else "slack"
        if output == "json":
            print(format_json(standup))
        else:
            print(format_slack(standup))

    elif cmd == "member":
        if len(sys.argv) < 3:
            print("Usage: standup_bot.py member <name>")
            sys.exit(1)
        name = sys.argv[2]
        members = parse_team_file()
        member = next((m for m in members if m.name.lower() == name.lower()), None)
        if not member:
            print(f"Member '{name}' not found in team.md")
            sys.exit(1)
        standup = generate_member_standup(member)
        print(json.dumps(asdict(standup), indent=2, default=str))

    elif cmd == "correlate":
        standup = generate_team_standup()
        if standup.cross_dependencies:
            print("Cross-dependencies detected:")
            for dep in standup.cross_dependencies:
                print(f"  [{dep['type']}] {dep['message']}")
        else:
            print("No cross-dependencies detected.")

    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)


if __name__ == "__main__":
    main()
