#!/usr/bin/env python3
"""
mirror/analyzer.py — Repository health analysis for GRIP MIRROR.

Analyses any GitHub repository's quality state without requiring a local
clone. Uses gh CLI for issues/PRs and rg for content patterns when the
repo is available locally.

The output is a dict suitable as the `state` argument to converge().
Higher quality_score = criterion is closer to being met.
"""

from __future__ import annotations

import json
import logging
import subprocess
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Quality signals — each contributes to the overall health score
MAX_ISSUES_P0 = 0       # No P0 issues = healthy
MAX_OPEN_PRS = 10       # Backlog grows stale beyond this
MIN_TEST_RATIO = 0.15   # At least 15% of files should be tests

# Priority label thresholds for multi-dimensional scoring
_HEALTH_P0_CEILING = 25
_HEALTH_P1_CEILING = 50
_HEALTH_P2_CEILING = 75


@dataclass
class RepoHealthDimensions:
    """Multi-dimensional repo health — maps to backlog scorer pattern.

    Five dimensions, each 0-20, summing to a max of 100.
    Mirrors the backlog_prioritiser's multi-signal approach applied
    to repository-level health instead of individual issues.
    """
    issue_health: int = 0       # 0-20: fewer P0/P1 = higher
    pr_velocity: int = 0        # 0-20: PR throughput, fewer stale
    ci_quality: int = 0         # 0-20: CI exists, tests pass
    code_quality: int = 0       # 0-20: test ratio, lint status
    community_health: int = 0   # 0-20: response time, contribution patterns
    total_score: int = 0        # Sum of above, max 100
    priority_label: str = "P2"  # Derived: P0 < 25, P1 < 50, P2 < 75, P3 >= 75

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class RepoHealth:
    """Snapshot of a repository's quality state.

    quality_score   : 0–100 composite health score (higher = better)
    open_issues     : count of open issues by priority label
    open_prs        : count of open PRs awaiting review
    stale_prs       : PRs open > 7 days without activity
    has_ci          : whether a CI workflow file exists
    test_ratio      : fraction of source files that are tests
                      (None = not measured / unmeasurable, NOT zero)
    p0_issues       : list of critical issue numbers
    p1_issues       : list of high-priority issue numbers
    repo            : target repository (owner/name)
    error           : non-empty if analysis failed
    """
    repo: str = ""
    quality_score: int = 0
    open_issues: dict = field(default_factory=dict)
    open_prs: int = 0
    stale_prs: int = 0
    has_ci: bool = False
    test_ratio: Optional[float] = None
    p0_issues: list = field(default_factory=list)
    p1_issues: list = field(default_factory=list)
    top_issue: Optional[dict] = None  # Highest-priority actionable issue
    dimensions: Optional[RepoHealthDimensions] = None  # Multi-dimensional breakdown
    error: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


def _gh(args: list[str], timeout: int = 20) -> tuple[int, str, str]:
    """Run a gh CLI command. Returns (returncode, stdout, stderr)."""
    try:
        result = subprocess.run(
            ["gh"] + args,
            capture_output=True, text=True, timeout=timeout,
        )
        return result.returncode, result.stdout, result.stderr
    except FileNotFoundError:
        return 1, "", "gh CLI not found"
    except subprocess.TimeoutExpired:
        return 1, "", f"gh command timed out after {timeout}s"


def analyze_repo(repo: str, local_path: Optional[str] = None) -> RepoHealth:
    """Analyse a GitHub repository's health state.

    Thin coordinator (cc-enforcement Coordinator pattern): each external
    fetch is a focused helper that mutates ``health``. Issue-fetch failure
    is fatal (return early); PR/CI failures degrade gracefully.

    Args:
        repo        : GitHub repo in "owner/name" format.
        local_path  : Optional local clone path for rg-based analysis.

    Returns:
        RepoHealth with quality_score and actionable issue data.
    """
    health = RepoHealth(repo=repo)
    if not _fetch_and_classify_issues(repo, health):
        return health  # fatal: issue fetch/parse failed (health.error set)
    _fetch_prs(repo, health)
    _check_ci(repo, health)
    if local_path and Path(local_path).exists():
        health.test_ratio = _compute_test_ratio(local_path)
    health.quality_score = _compute_quality_score(health)
    health.dimensions = compute_health_dimensions(health)
    return health


def _fetch_and_classify_issues(repo: str, health: RepoHealth) -> bool:
    """Fetch open issues, bucket by priority, set issue fields on ``health``.

    Returns False (and sets ``health.error``) on a fatal fetch/parse error
    so the coordinator can return early.
    """
    rc, stdout, err = _gh([
        "issue", "list", "--repo", repo, "--state", "open",
        "--limit", "50", "--json", "number,title,labels,createdAt",
    ])
    if rc != 0:
        health.error = f"gh issue list failed: {err[:200]}"
        logger.warning("[mirror.analyzer] %s", health.error)
        return False
    try:
        issues = json.loads(stdout) if stdout.strip() else []
    except json.JSONDecodeError as e:
        health.error = f"JSON parse error: {e}"
        return False

    p0, p1, p2, p3 = [], [], [], []
    buckets = {"P0": p0, "P1": p1, "P2": p2, "P3": p3}
    for issue in issues:
        labels = [l.get("name", "") for l in issue.get("labels", [])]
        buckets[_classify_issue_priority(labels)].append(
            {"number": issue["number"], "title": issue["title"]})

    health.p0_issues = [i["number"] for i in p0]
    health.p1_issues = [i["number"] for i in p1]
    health.open_issues = {
        "P0": len(p0), "P1": len(p1), "P2": len(p2), "P3": len(p3),
        "total": len(issues),
    }
    for group in (p0, p1, p2, p3):
        if group:
            health.top_issue = group[0]
            break
    return True


def _fetch_prs(repo: str, health: RepoHealth) -> None:
    """Fetch open PRs; set open_prs/stale_prs. Failure degrades silently."""
    rc, stdout, _ = _gh([
        "pr", "list", "--repo", repo, "--state", "open",
        "--limit", "20", "--json", "number,createdAt,updatedAt",
    ])
    if rc == 0 and stdout.strip():
        try:
            prs = json.loads(stdout)
            health.open_prs = len(prs)
            # Stale = no update in 7 days (simplified: open PRs > 5)
            health.stale_prs = max(0, len(prs) - 5)
        except json.JSONDecodeError as e:
            logging.warning(
                "[mirror.analyzer] PR list JSON parse error: %s", e)


def _check_ci(repo: str, health: RepoHealth) -> None:
    """Set health.has_ci from presence of .github/workflows."""
    rc, stdout, _ = _gh([
        "api", f"repos/{repo}/contents/.github/workflows", "--jq", "length",
    ])
    health.has_ci = (rc == 0 and stdout.strip() not in ("", "0"))


# Issue-label → priority. Recognises BOTH the P0/P1 scheme
# (GRIP/CodeTonight) AND conventional severity labels (bug/enhancement/
# roadmap — e.g. epam/ai-dial) so severity is not lost on repos that do not
# use P-labels. Heuristic by design: NOT evidence-from-issue-body inference
# (deliberately YAGNI for a stealth analyzer). Falsifiable: wrong if a
# repo's scheme uses none of these synonyms — then priority degrades to P3,
# never a false P0 (origin: pal uses P-labels, ai-dial uses bug/enhancement).
_P0_LABELS = frozenset({"p0", "critical", "security", "blocker",
                        "severity:critical"})
_P1_LABELS = frozenset({"p1", "bug", "regression", "high", "important",
                        "severity:high"})
_P2_LABELS = frozenset({"p2", "enhancement", "medium"})


def _classify_issue_priority(labels: list[str]) -> str:
    """Map a GitHub issue's labels to P0/P1/P2/P3 (case-insensitive).

    Scheme-agnostic: recognises P-prefixed labels and common severity
    synonyms. Unknown / feature / roadmap / docs → P3 (never a false P0).
    """
    low = {l.strip().lower() for l in labels}
    if low & _P0_LABELS:
        return "P0"
    if low & _P1_LABELS:
        return "P1"
    if low & _P2_LABELS:
        return "P2"
    return "P3"


def _compute_test_ratio(repo_path: str) -> Optional[float]:
    """Fraction of source files that are test files.

    Returns ``None`` when the ratio is *unmeasurable* — no source files
    enumerated, ``rg`` timed out / missing / exited non-zero — so callers
    can distinguish "unknown" from a genuine ``0.0`` ("source exists, no
    tests"). Conflating the two produced a false "untested" signal on
    heavily-tested repos whose enumeration timed out (origin: a
    pal-mcp-server grip-anywhere run reported 957 test fns as 0.0).
    """
    try:
        result = subprocess.run(
            ["rg", "--files", "--glob", "*.py", "--glob", "*.ts",
             "--glob", "*.js", "--glob", "!node_modules"],
            cwd=repo_path, capture_output=True, text=True, timeout=20,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None  # unmeasurable — NOT zero
    if result.returncode != 0:
        return None  # rg failed — unmeasurable, NOT zero
    all_files = [f for f in result.stdout.splitlines() if f]
    if not all_files:
        return None  # nothing enumerated — unmeasurable, NOT zero
    test_files = [f for f in all_files
                  if "test" in f.lower() or "spec" in f.lower()]
    return len(test_files) / len(all_files)


def _compute_quality_score(health: RepoHealth) -> int:
    """Composite quality score 0–100. Higher = healthier repo."""
    score = 100

    # P0 issues are critical blockers — heavy penalty
    score -= len(health.p0_issues) * 20

    # P1 issues are important
    score -= len(health.p1_issues) * 5

    # Stale PRs indicate process friction
    score -= health.stale_prs * 3

    # No CI is a significant gap
    if not health.has_ci:
        score -= 15

    # Test ratio below minimum. None = unmeasurable (NOT zero) → no
    # penalty: an unknown ratio must never be scored as if untested.
    if health.test_ratio is not None and 0 < health.test_ratio < MIN_TEST_RATIO:
        score -= 10

    return max(0, min(100, score))


def _code_quality_dim(test_ratio: Optional[float]) -> int:
    """Code-quality dimension from test ratio. ``None`` = unmeasurable
    (NOT zero) → neutral mid-range; genuine 0.0 keeps the prior mid-range
    default (behaviour unchanged). An unknown ratio is never penalised."""
    if test_ratio is None:
        return 10  # unknown — neutral, not penalised
    if test_ratio >= MIN_TEST_RATIO:
        return min(20, int(test_ratio * 100))
    if test_ratio > 0:
        return max(5, int(test_ratio * 100))
    return 10  # genuine 0.0 — mid-range default (unchanged)


def _community_health_dim(total_issues: int) -> int:
    """Community-health dimension from open-issue volume (tiered)."""
    if total_issues == 0:
        return 15  # no issues: inactive or clean — neutral
    if total_issues <= 10:
        return 18
    if total_issues <= 30:
        return 12
    return max(5, 20 - total_issues // 5)


def _priority_label(total_score: int) -> str:
    """Derive the P0–P3 priority label from the summed dimension score."""
    if total_score < _HEALTH_P0_CEILING:
        return "P0"
    if total_score < _HEALTH_P1_CEILING:
        return "P1"
    if total_score < _HEALTH_P2_CEILING:
        return "P2"
    return "P3"


def compute_health_dimensions(health: RepoHealth) -> RepoHealthDimensions:
    """Compute the multi-dimensional health breakdown (coordinator).

    Five dimensions (0-20 each), mirroring the backlog_prioritiser's
    multi-signal scoring pattern at the repository level.
    """
    dims = RepoHealthDimensions()

    issue_penalty = len(health.p0_issues) * 10 + len(health.p1_issues) * 3
    dims.issue_health = max(0, min(20, 20 - issue_penalty))

    pr_penalty = health.stale_prs * 4
    if health.open_prs > MAX_OPEN_PRS:
        pr_penalty += (health.open_prs - MAX_OPEN_PRS) * 2
    dims.pr_velocity = max(0, min(20, 20 - pr_penalty))

    dims.ci_quality = 20 if health.has_ci else 5
    dims.code_quality = _code_quality_dim(health.test_ratio)
    dims.community_health = _community_health_dim(
        health.open_issues.get("total", 0))

    dims.total_score = (
        dims.issue_health + dims.pr_velocity + dims.ci_quality
        + dims.code_quality + dims.community_health
    )
    dims.priority_label = _priority_label(dims.total_score)
    return dims


def quality_gate(health: RepoHealth, threshold: int = 80) -> bool:
    """Returns True when the repo's quality score meets the threshold."""
    return health.quality_score >= threshold
