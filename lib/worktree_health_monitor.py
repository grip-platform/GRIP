#!/usr/bin/env python3
"""
Worktree Health Monitor — detect stale, orphaned, and conflicting git worktrees.

Usage:
    python3 lib/worktree_health_monitor.py [--clean] [--dry-run] [--stale-hours N] [--json] [--verbose]

Requires: git
"""

import argparse
import json
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import List, Optional


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

HEALTHY = "HEALTHY"
STALE = "STALE"
ORPHANED = "ORPHANED"
CONFLICTING = "CONFLICTING"

ALL_STATUSES = {HEALTHY, STALE, ORPHANED, CONFLICTING}


@dataclass
class WorktreeInfo:
    """A single git worktree with health metadata."""

    path: str
    branch: Optional[str]  # None for detached HEAD
    head: str
    age_hours: float
    status: str
    disk_mb: float
    is_main: bool = False
    is_bare: bool = False
    is_detached: bool = False
    prunable: bool = False
    prunable_reason: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class HealthReport:
    """Aggregate health report for all worktrees."""

    worktrees: List[WorktreeInfo] = field(default_factory=list)
    total_disk_mb: float = 0.0
    stale_count: int = 0
    orphaned_count: int = 0
    conflicting_count: int = 0
    healthy_count: int = 0

    def to_dict(self) -> dict:
        return {
            "worktrees": [w.to_dict() for w in self.worktrees],
            "total_disk_mb": round(self.total_disk_mb, 2),
            "stale_count": self.stale_count,
            "orphaned_count": self.orphaned_count,
            "conflicting_count": self.conflicting_count,
            "healthy_count": self.healthy_count,
        }


# ---------------------------------------------------------------------------
# Git helpers
# ---------------------------------------------------------------------------


def _run_git(args: List[str], cwd: Optional[str] = None) -> subprocess.CompletedProcess:
    """Run a git command, return CompletedProcess."""
    cmd = ["git"] + args
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        cwd=cwd,
        timeout=30,
    )


def parse_porcelain_output(raw: str) -> List[dict]:
    """Parse ``git worktree list --porcelain`` output into a list of dicts.

    Each worktree block is separated by a blank line.  Keys:
      worktree, HEAD, branch, detached, bare, prunable

    Returns a list of dicts with string values (except bare/detached which are bool).
    """
    worktrees: List[dict] = []
    current: dict = {}

    for line in raw.splitlines():
        line = line.rstrip()
        if line == "":
            if current:
                worktrees.append(current)
                current = {}
            continue

        if line.startswith("worktree "):
            current["worktree"] = line[len("worktree "):]
        elif line.startswith("HEAD "):
            current["HEAD"] = line[len("HEAD "):]
        elif line.startswith("branch "):
            current["branch"] = line[len("branch "):]
        elif line == "detached":
            current["detached"] = True
        elif line == "bare":
            current["bare"] = True
        elif line.startswith("prunable "):
            current["prunable"] = line[len("prunable "):]

    # Flush last block if no trailing blank line
    if current:
        worktrees.append(current)

    return worktrees


def get_remote_branches(cwd: Optional[str] = None) -> set:
    """Return set of remote branch short names (e.g. 'origin/main')."""
    result = _run_git(["branch", "-r", "--format=%(refname:short)"], cwd=cwd)
    if result.returncode != 0:
        return set()
    return {b.strip() for b in result.stdout.splitlines() if b.strip()}


def get_local_branches(cwd: Optional[str] = None) -> set:
    """Return set of local branch names."""
    result = _run_git(["branch", "--format=%(refname:short)"], cwd=cwd)
    if result.returncode != 0:
        return set()
    return {b.strip() for b in result.stdout.splitlines() if b.strip()}


def get_merged_branches(cwd: Optional[str] = None) -> set:
    """Return set of branches already merged into HEAD.

    Two-tier predicate:
      1. `git branch --merged` — branches whose tip is reachable from HEAD.
         Cheap, offline. Produces a false negative for `--squash` and
         `--rebase` merges (the original branch tip is not an ancestor of
         the new squashed/rebased commit on main).
      2. `gh pr list --state merged` fallback — branches whose content
         shipped via a merged PR regardless of merge strategy. Catches the
         squash/rebase cases that (1) misses. Network call; degrades to
         empty set on missing gh, no network, or non-GitHub remote.

    Union of both is the canonical "is this branch merged?" predicate.
    """
    branches: set = set()

    result = _run_git(["branch", "--merged", "--format=%(refname:short)"], cwd=cwd)
    if result.returncode == 0:
        branches.update(b.strip() for b in result.stdout.splitlines() if b.strip())

    branches.update(_get_squash_merged_branches(cwd=cwd))

    return branches


def _get_squash_merged_branches(cwd: Optional[str] = None) -> set:
    """Squash/rebase-merge fallback for get_merged_branches.

    Queries `gh pr list --state merged` to find branches whose PRs landed
    on the default branch via squash or rebase. Returns empty set on any
    failure (gh not installed, no network, repo not on GitHub, parse error)
    so callers can union safely.
    """
    try:
        proc = subprocess.run(
            ["gh", "pr", "list", "--state", "merged", "--limit", "200",
             "--json", "headRefName"],
            cwd=cwd, capture_output=True, text=True, timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return set()
    if proc.returncode != 0:
        return set()
    try:
        data = json.loads(proc.stdout) if proc.stdout.strip() else []
    except (json.JSONDecodeError, ValueError):
        return set()
    return {entry.get("headRefName", "").strip() for entry in data
            if isinstance(entry, dict) and entry.get("headRefName")}


def get_last_commit_timestamp(head: str, cwd: Optional[str] = None) -> Optional[float]:
    """Return Unix timestamp of HEAD commit, or None on failure."""
    result = _run_git(["log", "-1", "--format=%ct", head], cwd=cwd)
    if result.returncode != 0 or not result.stdout.strip():
        return None
    try:
        return float(result.stdout.strip())
    except ValueError:
        return None


def get_default_branch(cwd: Optional[str] = None) -> str:
    """Detect the default branch name (main or master)."""
    result = _run_git(["symbolic-ref", "refs/remotes/origin/HEAD"], cwd=cwd)
    if result.returncode == 0:
        ref = result.stdout.strip()
        # refs/remotes/origin/main -> main
        return ref.split("/")[-1]
    # Fallback: check if main exists
    local = get_local_branches(cwd)
    if "main" in local:
        return "main"
    if "master" in local:
        return "master"
    return "main"


# ---------------------------------------------------------------------------
# Disk usage
# ---------------------------------------------------------------------------


def estimate_disk_mb(path: str) -> float:
    """Estimate disk usage of a worktree directory in MB, skipping .git.

    Returns 0.0 if path doesn't exist or is not a directory.
    """
    root = Path(path)
    if not root.is_dir():
        return 0.0

    try:
        total_bytes = sum(
            _safe_filesize(f)
            for f in root.rglob("*")
            if f.is_file() and ".git" not in f.relative_to(root).parts
        )
    except OSError:
        total_bytes = 0

    return total_bytes / (1024 * 1024)


def _safe_filesize(path: Path) -> int:
    """Return file size in bytes; 0 on stat failure (broken symlink, race)."""
    try:
        return path.stat().st_size
    except OSError:
        return 0


# ---------------------------------------------------------------------------
# Health classification
# ---------------------------------------------------------------------------


def classify_worktrees(
    parsed: List[dict],
    stale_hours: float = 24.0,
    remote_branches: Optional[set] = None,
    merged_branches: Optional[set] = None,
    repo_cwd: Optional[str] = None,
    now: Optional[float] = None,
) -> List[WorktreeInfo]:
    """Classify each parsed worktree block into a WorktreeInfo with status.

    Priority: CONFLICTING > ORPHANED > STALE > HEALTHY
    The main worktree is always HEALTHY.
    """
    if now is None:
        now = time.time()

    infos: List[WorktreeInfo] = []

    # Branch -> list of worktree paths (for conflict detection)
    branch_map: dict = {}

    for i, wt in enumerate(parsed):
        wt_path = wt.get("worktree", "")
        head = wt.get("HEAD", "0" * 40)
        raw_branch = wt.get("branch")
        is_bare = wt.get("bare", False)
        is_detached = wt.get("detached", False)
        prunable = "prunable" in wt
        prunable_reason = wt.get("prunable")

        # Extract short branch name from refs/heads/xxx
        branch_name = None
        if raw_branch:
            if raw_branch.startswith("refs/heads/"):
                branch_name = raw_branch[len("refs/heads/"):]
            else:
                branch_name = raw_branch

        # Is this the main worktree? (first entry is always the main worktree)
        is_main = i == 0

        # Age calculation
        ts = get_last_commit_timestamp(head, cwd=repo_cwd)
        if ts is not None:
            age_hours = (now - ts) / 3600.0
        else:
            age_hours = 0.0

        # Disk usage
        disk_mb = estimate_disk_mb(wt_path)

        # Track branches for conflict detection
        if branch_name:
            branch_map.setdefault(branch_name, []).append(wt_path)

        info = WorktreeInfo(
            path=wt_path,
            branch=branch_name,
            head=head,
            age_hours=round(age_hours, 2),
            status=HEALTHY,  # Will be updated below
            disk_mb=round(disk_mb, 2),
            is_main=is_main,
            is_bare=is_bare,
            is_detached=is_detached,
            prunable=prunable,
            prunable_reason=prunable_reason,
        )
        infos.append(info)

    # --- Status classification ---
    # 1) Mark conflicts (same branch in multiple worktrees)
    conflicting_paths: set = set()
    for branch, paths in branch_map.items():
        if len(paths) > 1:
            conflicting_paths.update(paths)

    for info in infos:
        # Main worktree is always healthy
        if info.is_main or info.is_bare:
            info.status = HEALTHY
            continue

        # Conflicting takes highest priority
        if info.path in conflicting_paths:
            info.status = CONFLICTING
            continue

        # Orphaned: branch deleted on remote or already merged
        if info.branch and not info.is_detached:
            is_orphaned = False
            if remote_branches is not None:
                # Check if any remote has this branch
                has_remote = any(
                    rb.endswith("/" + info.branch) for rb in remote_branches
                )
                # Also check if branch is merged
                is_merged = (
                    merged_branches is not None and info.branch in merged_branches
                )
                if not has_remote or is_merged:
                    is_orphaned = True

            if is_orphaned:
                info.status = ORPHANED
                continue

        # Stale: older than threshold
        if info.age_hours >= stale_hours:
            info.status = STALE
            continue

        # Prunable worktrees that git itself marks
        if info.prunable:
            info.status = STALE
            continue

        info.status = HEALTHY

    return infos


def build_report(worktrees: List[WorktreeInfo]) -> HealthReport:
    """Build an aggregate HealthReport from a list of WorktreeInfo."""
    report = HealthReport()
    report.worktrees = worktrees
    report.total_disk_mb = round(sum(w.disk_mb for w in worktrees), 2)
    report.stale_count = sum(1 for w in worktrees if w.status == STALE)
    report.orphaned_count = sum(1 for w in worktrees if w.status == ORPHANED)
    report.conflicting_count = sum(1 for w in worktrees if w.status == CONFLICTING)
    report.healthy_count = sum(1 for w in worktrees if w.status == HEALTHY)
    return report


# ---------------------------------------------------------------------------
# Scanning (full pipeline)
# ---------------------------------------------------------------------------


def scan_worktrees(
    stale_hours: float = 24.0,
    repo_cwd: Optional[str] = None,
    now: Optional[float] = None,
) -> HealthReport:
    """Scan all git worktrees and return a HealthReport.

    ``repo_cwd`` should be a path inside any git repository (the main
    worktree or any linked worktree).  If None, uses the current directory.
    """
    result = _run_git(["worktree", "list", "--porcelain"], cwd=repo_cwd)
    if result.returncode != 0:
        raise RuntimeError(f"git worktree list failed: {result.stderr.strip()}")

    parsed = parse_porcelain_output(result.stdout)
    if not parsed:
        return HealthReport()

    # Gather remote + merged branch info from the main worktree
    main_path = parsed[0].get("worktree") if parsed else repo_cwd
    remote_branches = get_remote_branches(cwd=main_path)
    merged_branches = get_merged_branches(cwd=main_path)

    worktrees = classify_worktrees(
        parsed,
        stale_hours=stale_hours,
        remote_branches=remote_branches,
        merged_branches=merged_branches,
        repo_cwd=main_path,
        now=now,
    )

    return build_report(worktrees)


# ---------------------------------------------------------------------------
# Clean
# ---------------------------------------------------------------------------


def clean_worktrees(
    report: HealthReport,
    dry_run: bool = True,
    verbose: bool = False,
) -> List[str]:
    """Remove worktrees that are STALE or ORPHANED.

    Returns list of paths that were (or would be) removed.
    Never removes HEALTHY or CONFLICTING worktrees.
    Never removes the main worktree.
    """
    removed: List[str] = []

    for wt in report.worktrees:
        if wt.is_main or wt.is_bare:
            continue
        if wt.status not in (STALE, ORPHANED):
            continue

        removed.append(wt.path)

        if verbose:
            action = "Would remove" if dry_run else "Removing"
            print(f"  {action}: {wt.path} ({wt.status}, {wt.age_hours:.1f}h, {wt.disk_mb:.1f}MB)")

        if not dry_run:
            # First try git worktree remove
            result = _run_git(["worktree", "remove", "--force", wt.path])
            if result.returncode != 0:
                # Fallback: manual removal + prune
                wt_path = Path(wt.path)
                if wt_path.exists():
                    shutil.rmtree(str(wt_path))
                _run_git(["worktree", "prune"])

    return removed


# ---------------------------------------------------------------------------
# Output formatting
# ---------------------------------------------------------------------------

STATUS_LABELS = {
    HEALTHY: "OK",
    STALE: "STALE",
    ORPHANED: "ORPHAN",
    CONFLICTING: "CONFLICT",
}


def format_markdown_report(report: HealthReport) -> str:
    """Format the health report as a markdown table."""
    lines: List[str] = []
    lines.append("# Worktree Health Report")
    lines.append("")
    lines.append(
        f"**Total**: {len(report.worktrees)} worktrees, "
        f"{report.total_disk_mb:.1f} MB disk usage"
    )
    lines.append(
        f"**Health**: {report.healthy_count} healthy, "
        f"{report.stale_count} stale, "
        f"{report.orphaned_count} orphaned, "
        f"{report.conflicting_count} conflicting"
    )
    lines.append("")

    # Table header
    lines.append("| Status | Branch | Age (h) | Disk (MB) | Path |")
    lines.append("|--------|--------|---------|-----------|------|")

    for wt in report.worktrees:
        status = STATUS_LABELS.get(wt.status, wt.status)
        branch = wt.branch or "(detached)"
        if wt.is_main:
            branch = f"{branch} [main]"
        lines.append(
            f"| {status} | {branch} | {wt.age_hours:.1f} | {wt.disk_mb:.1f} | `{wt.path}` |"
        )

    lines.append("")
    return "\n".join(lines)


def format_json_report(report: HealthReport) -> str:
    """Format the health report as JSON."""
    return json.dumps(report.to_dict(), indent=2)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Git Worktree Health Monitor -- detect stale, orphaned, and conflicting worktrees.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s                     Show health report (markdown)
  %(prog)s --json              Output as JSON
  %(prog)s --stale-hours 48    Treat worktrees >48h as stale
  %(prog)s --clean --dry-run   Show what would be cleaned
  %(prog)s --clean             Remove stale/orphaned worktrees
""",
    )
    parser.add_argument(
        "--clean",
        action="store_true",
        help="Remove stale and orphaned worktrees",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be removed without actually removing (use with --clean)",
    )
    parser.add_argument(
        "--stale-hours",
        type=float,
        default=24.0,
        metavar="N",
        help="Hours after which a worktree is considered stale (default: 24)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output report as JSON instead of markdown",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Show detailed output during operations",
    )
    parser.add_argument(
        "--repo",
        type=str,
        default=None,
        metavar="PATH",
        help="Path to git repository (default: current directory)",
    )
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    """CLI entry point. Returns exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        report = scan_worktrees(
            stale_hours=args.stale_hours,
            repo_cwd=args.repo,
        )
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    if args.clean:
        removed = clean_worktrees(
            report,
            dry_run=args.dry_run,
            verbose=args.verbose,
        )
        action = "Would remove" if args.dry_run else "Removed"
        print(f"{action} {len(removed)} worktree(s)")
        if removed and args.verbose:
            for p in removed:
                print(f"  - {p}")
        if not args.dry_run and removed:
            # Re-scan after cleaning for accurate report
            try:
                report = scan_worktrees(
                    stale_hours=args.stale_hours,
                    repo_cwd=args.repo,
                )
            except RuntimeError:
                pass

    if args.json:
        print(format_json_report(report))
    else:
        print(format_markdown_report(report))

    # Exit code: 0 if all healthy, 1 if any issues
    if report.stale_count + report.orphaned_count + report.conflicting_count > 0:
        return 2

    return 0


if __name__ == "__main__":
    sys.exit(main())
