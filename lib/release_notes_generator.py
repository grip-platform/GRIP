#!/usr/bin/env python3
"""Release Notes Generator — auto-generate from merged PRs.

Aggregates PRs by conventional commit type, contributor attribution,
statistics, breaking change detection. Markdown/JSON/plain text output.

Usage:
    python3 lib/release_notes_generator.py                     # since last tag
    python3 lib/release_notes_generator.py --from v1.0 --to v2.0
    python3 lib/release_notes_generator.py --from HEAD~50
    python3 lib/release_notes_generator.py --json
    python3 lib/release_notes_generator.py --contributors
"""

import argparse
import json
import re
import subprocess
import sys
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from typing import Optional


# Conventional commit type -> display heading
TYPE_HEADINGS = {
    "feat": "Features",
    "fix": "Bug Fixes",
    "chore": "Chores",
    "test": "Tests",
    "refactor": "Refactoring",
    "docs": "Documentation",
    "perf": "Performance",
    "style": "Style",
    "ci": "CI/CD",
    "build": "Build",
}

# Regex for conventional commit: type(scope)!: description
CONVENTIONAL_RE = re.compile(
    r"^(?P<type>[a-z]+)"
    r"(?:\((?P<scope>[^)]*)\))?"
    r"(?P<breaking>!)?"
    r":\s*(?P<description>.+)$",
    re.IGNORECASE,
)

# Extract PR number from merge commit messages
PR_NUMBER_RE = re.compile(r"#(\d+)")


@dataclass
class CommitInfo:
    sha: str
    author: str
    date: str
    subject: str
    body: str = ""
    commit_type: str = "other"
    scope: Optional[str] = None
    description: str = ""
    is_breaking: bool = False
    pr_number: Optional[int] = None
    is_merge: bool = False


@dataclass
class Stats:
    total_commits: int = 0
    total_merges: int = 0
    files_changed: int = 0
    insertions: int = 0
    deletions: int = 0


@dataclass
class Contributor:
    name: str
    commit_count: int = 0
    types: dict = field(default_factory=dict)


@dataclass
class ReleaseNotes:
    from_ref: str
    to_ref: str
    commits: list = field(default_factory=list)
    categorized: dict = field(default_factory=dict)
    breaking_changes: list = field(default_factory=list)
    contributors: dict = field(default_factory=dict)
    stats: Stats = field(default_factory=Stats)
    repo_url: str = ""


def run_git(args: list[str], cwd: Optional[str] = None) -> str:
    """Run a git command and return stdout."""
    result = subprocess.run(
        ["git"] + args,
        capture_output=True,
        text=True,
        cwd=cwd,
        timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def get_latest_tag(cwd: Optional[str] = None) -> Optional[str]:
    """Get the most recent tag reachable from HEAD."""
    try:
        return run_git(["describe", "--tags", "--abbrev=0"], cwd=cwd)
    except RuntimeError:
        return None


def get_repo_url(cwd: Optional[str] = None) -> str:
    """Extract GitHub repo URL from remote origin."""
    try:
        remote = run_git(["remote", "get-url", "origin"], cwd=cwd)
        # Convert SSH to HTTPS
        if remote.startswith("git@github.com:"):
            remote = remote.replace("git@github.com:", "https://github.com/")
        if remote.endswith(".git"):
            remote = remote[:-4]
        return remote
    except RuntimeError:
        return ""


def parse_commit_message(subject: str, body: str = "") -> tuple[str, Optional[str], str, bool]:
    """Parse a conventional commit message.

    Returns (type, scope, description, is_breaking).
    """
    match = CONVENTIONAL_RE.match(subject)
    if match:
        commit_type = match.group("type").lower()
        scope = match.group("scope")
        breaking = bool(match.group("breaking"))
        description = match.group("description")

        # Check body for BREAKING CHANGE footer
        if not breaking and body:
            if "BREAKING CHANGE" in body or "BREAKING-CHANGE" in body:
                breaking = True

        return commit_type, scope, description, breaking

    # Not a conventional commit — classify as "other"
    return "other", None, subject, False


def extract_pr_number(subject: str) -> Optional[int]:
    """Extract PR number from commit message (e.g. 'Merge pull request #42')."""
    match = PR_NUMBER_RE.search(subject)
    if match:
        return int(match.group(1))
    return None


def parse_git_log(log_output: str) -> list[CommitInfo]:
    """Parse git log output with custom format into CommitInfo objects.

    Expected format per entry (separated by COMMIT_SEP):
        sha<FIELD_SEP>author<FIELD_SEP>date<FIELD_SEP>subject<FIELD_SEP>body<FIELD_SEP>parents
    """
    if not log_output.strip():
        return []

    commits = []
    entries = log_output.split("---COMMIT_SEP---")

    for entry in entries:
        entry = entry.strip()
        if not entry:
            continue

        parts = entry.split("---FIELD_SEP---")
        if len(parts) < 4:
            continue

        sha = parts[0].strip()
        author = parts[1].strip()
        date = parts[2].strip()
        subject = parts[3].strip()
        body = parts[4].strip() if len(parts) > 4 else ""
        parents = parts[5].strip() if len(parts) > 5 else ""

        is_merge = len(parents.split()) > 1

        commit_type, scope, description, is_breaking = parse_commit_message(subject, body)
        pr_number = extract_pr_number(subject)

        commits.append(CommitInfo(
            sha=sha,
            author=author,
            date=date,
            subject=subject,
            body=body,
            commit_type=commit_type,
            scope=scope,
            description=description,
            is_breaking=is_breaking,
            pr_number=pr_number,
            is_merge=is_merge,
        ))

    return commits


def parse_diffstat(diffstat_output: str) -> tuple[int, int, int]:
    """Parse git diff --stat output for files changed, insertions, deletions.

    The summary line looks like:
        10 files changed, 200 insertions(+), 50 deletions(-)
    """
    files_changed = 0
    insertions = 0
    deletions = 0

    if not diffstat_output.strip():
        return files_changed, insertions, deletions

    # Look for the summary line
    for line in diffstat_output.strip().splitlines():
        line = line.strip()
        m_files = re.search(r"(\d+)\s+files?\s+changed", line)
        m_ins = re.search(r"(\d+)\s+insertions?\(\+\)", line)
        m_del = re.search(r"(\d+)\s+deletions?\(-\)", line)
        if m_files:
            files_changed = int(m_files.group(1))
        if m_ins:
            insertions = int(m_ins.group(1))
        if m_del:
            deletions = int(m_del.group(1))

    return files_changed, insertions, deletions


GIT_LOG_FORMAT = (
    "---COMMIT_SEP---%H---FIELD_SEP---%aN---FIELD_SEP---%aI---FIELD_SEP---"
    "%s---FIELD_SEP---%b---FIELD_SEP---%P"
)


def gather_commits(from_ref: str, to_ref: str, cwd: Optional[str] = None) -> list[CommitInfo]:
    """Gather all commits in a ref range."""
    log_output = run_git(
        ["log", f"{from_ref}..{to_ref}", f"--format={GIT_LOG_FORMAT}"],
        cwd=cwd,
    )
    return parse_git_log(log_output)


def gather_stats(from_ref: str, to_ref: str, commits: list[CommitInfo],
                 cwd: Optional[str] = None) -> Stats:
    """Gather statistics for a ref range."""
    try:
        diffstat = run_git(
            ["diff", "--stat", from_ref, to_ref],
            cwd=cwd,
        )
        files_changed, insertions, deletions = parse_diffstat(diffstat)
    except RuntimeError:
        files_changed, insertions, deletions = 0, 0, 0

    merge_count = sum(1 for c in commits if c.is_merge)

    return Stats(
        total_commits=len(commits),
        total_merges=merge_count,
        files_changed=files_changed,
        insertions=insertions,
        deletions=deletions,
    )


def categorize_commits(commits: list[CommitInfo]) -> dict[str, list[CommitInfo]]:
    """Group commits by conventional commit type."""
    categorized = defaultdict(list)
    for commit in commits:
        categorized[commit.commit_type].append(commit)
    return dict(categorized)


def find_breaking_changes(commits: list[CommitInfo]) -> list[CommitInfo]:
    """Find all commits flagged as breaking changes."""
    return [c for c in commits if c.is_breaking]


def gather_contributors(commits: list[CommitInfo]) -> dict[str, Contributor]:
    """Aggregate contributor statistics."""
    contributors: dict[str, Contributor] = {}
    for commit in commits:
        name = commit.author
        if name not in contributors:
            contributors[name] = Contributor(name=name)
        contributors[name].commit_count += 1
        t = commit.commit_type
        contributors[name].types[t] = contributors[name].types.get(t, 0) + 1
    return contributors


def generate_release_notes(
    from_ref: Optional[str] = None,
    to_ref: str = "HEAD",
    cwd: Optional[str] = None,
) -> ReleaseNotes:
    """Generate release notes for a ref range."""
    # Resolve from_ref to latest tag if not specified
    if from_ref is None:
        from_ref = get_latest_tag(cwd=cwd)
        if from_ref is None:
            # No tags — use initial commit
            from_ref = run_git(["rev-list", "--max-parents=0", "HEAD"], cwd=cwd).splitlines()[0]

    repo_url = get_repo_url(cwd=cwd)
    commits = gather_commits(from_ref, to_ref, cwd=cwd)
    categorized = categorize_commits(commits)
    breaking = find_breaking_changes(commits)
    contributors = gather_contributors(commits)
    stats = gather_stats(from_ref, to_ref, commits, cwd=cwd)

    return ReleaseNotes(
        from_ref=from_ref,
        to_ref=to_ref,
        commits=commits,
        categorized=categorized,
        breaking_changes=breaking,
        contributors=contributors,
        stats=stats,
        repo_url=repo_url,
    )


# --- Output Formatters ---


def _pr_link(pr_number: Optional[int], repo_url: str) -> str:
    """Generate a PR link if possible."""
    if pr_number and repo_url:
        return f"[#{pr_number}]({repo_url}/pull/{pr_number})"
    elif pr_number:
        return f"#{pr_number}"
    return ""


def _commit_line(commit: CommitInfo, repo_url: str) -> str:
    """Format a single commit line for markdown."""
    parts = []
    if commit.scope:
        parts.append(f"**{commit.scope}**: ")
    parts.append(commit.description or commit.subject)

    pr_link = _pr_link(commit.pr_number, repo_url)
    if pr_link:
        parts.append(f" ({pr_link})")

    sha_short = commit.sha[:7]
    if repo_url:
        parts.append(f" ([{sha_short}]({repo_url}/commit/{commit.sha}))")
    else:
        parts.append(f" ({sha_short})")

    return "- " + "".join(parts)


def format_markdown(notes: ReleaseNotes, include_contributors: bool = False) -> str:
    """Format release notes as Markdown."""
    lines = []
    lines.append(f"# Release Notes ({notes.from_ref}..{notes.to_ref})")
    lines.append("")

    # Breaking changes first
    if notes.breaking_changes:
        lines.append("## BREAKING CHANGES")
        lines.append("")
        for commit in notes.breaking_changes:
            lines.append(_commit_line(commit, notes.repo_url))
        lines.append("")

    # Categorized sections — ordered by TYPE_HEADINGS, then remaining
    ordered_types = list(TYPE_HEADINGS.keys())
    seen = set()

    for ctype in ordered_types:
        if ctype in notes.categorized:
            seen.add(ctype)
            heading = TYPE_HEADINGS.get(ctype, ctype.title())
            lines.append(f"## {heading}")
            lines.append("")
            for commit in notes.categorized[ctype]:
                lines.append(_commit_line(commit, notes.repo_url))
            lines.append("")

    # Any remaining types not in the standard list
    for ctype in sorted(notes.categorized.keys()):
        if ctype not in seen:
            heading = TYPE_HEADINGS.get(ctype, ctype.title())
            lines.append(f"## {heading}")
            lines.append("")
            for commit in notes.categorized[ctype]:
                lines.append(_commit_line(commit, notes.repo_url))
            lines.append("")

    # Statistics
    lines.append("## Statistics")
    lines.append("")
    lines.append(f"- **Commits**: {notes.stats.total_commits}")
    lines.append(f"- **Merges**: {notes.stats.total_merges}")
    lines.append(f"- **Files changed**: {notes.stats.files_changed}")
    lines.append(f"- **Lines added**: {notes.stats.insertions}")
    lines.append(f"- **Lines removed**: {notes.stats.deletions}")
    lines.append("")

    # Contributors
    if include_contributors and notes.contributors:
        lines.append("## Contributors")
        lines.append("")
        sorted_contribs = sorted(
            notes.contributors.values(),
            key=lambda c: c.commit_count,
            reverse=True,
        )
        for contrib in sorted_contribs:
            lines.append(f"- **{contrib.name}** ({contrib.commit_count} commits)")
        lines.append("")

    return "\n".join(lines)


def format_plain(notes: ReleaseNotes, include_contributors: bool = False) -> str:
    """Format release notes as plain text."""
    lines = []
    lines.append(f"Release Notes ({notes.from_ref}..{notes.to_ref})")
    lines.append("=" * len(lines[0]))
    lines.append("")

    if notes.breaking_changes:
        lines.append("BREAKING CHANGES")
        lines.append("-" * 16)
        for commit in notes.breaking_changes:
            pr = f" (#{commit.pr_number})" if commit.pr_number else ""
            desc = commit.description or commit.subject
            scope_prefix = f"{commit.scope}: " if commit.scope else ""
            lines.append(f"  * {scope_prefix}{desc}{pr}")
        lines.append("")

    ordered_types = list(TYPE_HEADINGS.keys())
    seen = set()

    for ctype in ordered_types:
        if ctype in notes.categorized:
            seen.add(ctype)
            heading = TYPE_HEADINGS.get(ctype, ctype.title())
            lines.append(heading)
            lines.append("-" * len(heading))
            for commit in notes.categorized[ctype]:
                pr = f" (#{commit.pr_number})" if commit.pr_number else ""
                desc = commit.description or commit.subject
                scope_prefix = f"{commit.scope}: " if commit.scope else ""
                lines.append(f"  * {scope_prefix}{desc}{pr}")
            lines.append("")

    for ctype in sorted(notes.categorized.keys()):
        if ctype not in seen:
            heading = TYPE_HEADINGS.get(ctype, ctype.title())
            lines.append(heading)
            lines.append("-" * len(heading))
            for commit in notes.categorized[ctype]:
                pr = f" (#{commit.pr_number})" if commit.pr_number else ""
                desc = commit.description or commit.subject
                scope_prefix = f"{commit.scope}: " if commit.scope else ""
                lines.append(f"  * {scope_prefix}{desc}{pr}")
            lines.append("")

    lines.append("Statistics")
    lines.append("-" * 10)
    lines.append(f"  Commits:       {notes.stats.total_commits}")
    lines.append(f"  Merges:        {notes.stats.total_merges}")
    lines.append(f"  Files changed: {notes.stats.files_changed}")
    lines.append(f"  Lines added:   {notes.stats.insertions}")
    lines.append(f"  Lines removed: {notes.stats.deletions}")
    lines.append("")

    if include_contributors and notes.contributors:
        lines.append("Contributors")
        lines.append("-" * 12)
        sorted_contribs = sorted(
            notes.contributors.values(),
            key=lambda c: c.commit_count,
            reverse=True,
        )
        for contrib in sorted_contribs:
            lines.append(f"  {contrib.name} ({contrib.commit_count} commits)")
        lines.append("")

    return "\n".join(lines)


def _commit_to_dict(commit: CommitInfo) -> dict:
    """Convert CommitInfo to a JSON-serializable dict."""
    return {
        "sha": commit.sha,
        "author": commit.author,
        "date": commit.date,
        "subject": commit.subject,
        "type": commit.commit_type,
        "scope": commit.scope,
        "description": commit.description,
        "is_breaking": commit.is_breaking,
        "pr_number": commit.pr_number,
        "is_merge": commit.is_merge,
    }


def format_json(notes: ReleaseNotes, include_contributors: bool = False) -> str:
    """Format release notes as JSON."""
    data = {
        "from_ref": notes.from_ref,
        "to_ref": notes.to_ref,
        "stats": asdict(notes.stats),
        "breaking_changes": [_commit_to_dict(c) for c in notes.breaking_changes],
        "categories": {},
    }

    for ctype, commits in notes.categorized.items():
        heading = TYPE_HEADINGS.get(ctype, ctype.title())
        data["categories"][ctype] = {
            "heading": heading,
            "commits": [_commit_to_dict(c) for c in commits],
        }

    if include_contributors:
        data["contributors"] = {
            name: {"name": c.name, "commit_count": c.commit_count, "types": c.types}
            for name, c in sorted(
                notes.contributors.items(),
                key=lambda item: item[1].commit_count,
                reverse=True,
            )
        }

    return json.dumps(data, indent=2)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate release notes from merged PRs and commits."
    )
    parser.add_argument(
        "--from", dest="from_ref", default=None,
        help="Start ref (tag, commit, branch). Default: latest tag.",
    )
    parser.add_argument(
        "--to", dest="to_ref", default="HEAD",
        help="End ref. Default: HEAD.",
    )
    parser.add_argument(
        "--json", dest="output_json", action="store_true",
        help="Output as JSON.",
    )
    parser.add_argument(
        "--plain", dest="output_plain", action="store_true",
        help="Output as plain text.",
    )
    parser.add_argument(
        "--contributors", action="store_true",
        help="Include contributor section.",
    )
    parser.add_argument(
        "--cwd", default=None,
        help="Git repository directory (default: current directory).",
    )

    args = parser.parse_args(argv)

    try:
        notes = generate_release_notes(
            from_ref=args.from_ref,
            to_ref=args.to_ref,
            cwd=args.cwd,
        )
    except RuntimeError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    include_contributors = args.contributors

    if args.output_json:
        output = format_json(notes, include_contributors)
    elif args.output_plain:
        output = format_plain(notes, include_contributors)
    else:
        output = format_markdown(notes, include_contributors)

    print(output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
