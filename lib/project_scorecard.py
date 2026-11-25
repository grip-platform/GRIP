"""Project Health Scorecard - aggregate dashboard combining multiple data sources.

Produces a single health score with letter grades across 7 dimensions:
Code Quality, Velocity, Debt, Documentation, Security, Team Health, Innovation.
"""

import json
import os
import subprocess
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional


# Default weights for each dimension (must sum to 1.0)
DEFAULT_WEIGHTS = {
    "code_quality": 0.20,
    "velocity": 0.15,
    "debt": 0.15,
    "documentation": 0.10,
    "security": 0.15,
    "team_health": 0.10,
    "innovation": 0.15,
}

# Grade thresholds
GRADE_THRESHOLDS = [
    (97, "A+"), (93, "A"), (90, "A-"),
    (87, "B+"), (83, "B"), (80, "B-"),
    (77, "C+"), (73, "C"), (70, "C-"),
    (67, "D+"), (63, "D"), (60, "D-"),
    (0, "F"),
]

# Recommendations per dimension when score is low
RECOMMENDATIONS = {
    "code_quality": [
        "Increase test coverage - aim for 1:1 test-to-source ratio",
        "Run linter regularly and address warnings",
        "Add pre-commit hooks for automated quality checks",
    ],
    "velocity": [
        "Break large PRs into smaller, reviewable chunks",
        "Set daily commit goals for active contributors",
        "Triage and close stale issues weekly",
    ],
    "debt": [
        "Schedule regular TODO/FIXME cleanup sprints",
        "Close or merge stale PRs older than 30 days",
        "Prioritize bug fixes before new features",
    ],
    "documentation": [
        "Add README files to undocumented directories",
        "Keep README updated with each major change",
        "Add inline documentation for complex functions",
    ],
    "security": [
        "Run secrets scanner before every commit",
        "Update dependencies monthly",
        "Enable Dependabot or similar automated scanning",
    ],
    "team_health": [
        "Aim for <24h PR review turnaround",
        "Distribute code ownership across team members",
        "Rotate reviewers to spread knowledge",
    ],
    "innovation": [
        "Allocate time for experimental features",
        "Track feature-to-fix ratio weekly",
        "Celebrate new file creation as a health signal",
    ],
}


@dataclass
class DimensionScore:
    """Score for a single health dimension."""
    name: str
    score: float  # 0-100
    grade: str
    details: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ScorecardSnapshot:
    """Point-in-time scorecard snapshot."""
    timestamp: str
    overall_score: float
    overall_grade: str
    dimensions: dict  # name -> score
    repo_path: str
    weights: dict

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict())

    @classmethod
    def from_dict(cls, data: dict) -> "ScorecardSnapshot":
        return cls(**data)


def score_to_grade(score: float) -> str:
    """Convert a 0-100 score to a letter grade."""
    clamped = max(0.0, min(100.0, score))
    for threshold, grade in GRADE_THRESHOLDS:
        if clamped >= threshold:
            return grade
    return "F"


def clamp_score(value: float) -> float:
    """Clamp a score to 0-100 range."""
    return max(0.0, min(100.0, value))


class ProjectScorecard:
    """Aggregate project health scorecard.

    Combines git, GitHub, and filesystem data into a single health score
    with letter grades across 7 dimensions.
    """

    def __init__(
        self,
        repo_path: str = ".",
        weights: Optional[dict] = None,
        history_path: Optional[str] = None,
        targets: Optional[dict] = None,
    ):
        self.repo_path = os.path.abspath(repo_path)
        self.weights = weights or dict(DEFAULT_WEIGHTS)
        self.history_path = history_path or os.path.join(
            self.repo_path, ".project-scorecard-history.jsonl"
        )
        self.targets = targets or {dim: 85.0 for dim in DEFAULT_WEIGHTS}
        self._dimensions: dict[str, DimensionScore] = {}
        self._validate_weights()

    def _validate_weights(self) -> None:
        """Validate that weights sum to ~1.0 and all dimensions present."""
        total = sum(self.weights.values())
        if abs(total - 1.0) > 0.01:
            raise ValueError(
                f"Weights must sum to 1.0, got {total:.4f}"
            )
        for dim in DEFAULT_WEIGHTS:
            if dim not in self.weights:
                raise ValueError(f"Missing weight for dimension: {dim}")

    def _run_git(self, *args: str, timeout: int = 30) -> str:
        """Run a git command and return stdout."""
        try:
            result = subprocess.run(
                ["git", *args],
                cwd=self.repo_path,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            return result.stdout.strip()
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            return ""

    def _run_gh(self, *args: str, timeout: int = 30) -> str:
        """Run a gh CLI command and return stdout."""
        try:
            result = subprocess.run(
                ["gh", *args],
                cwd=self.repo_path,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            return result.stdout.strip()
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            return ""

    # --- Dimension Scorers ---

    def _score_code_quality(self) -> DimensionScore:
        """Score code quality based on test/source ratio and lint indicators."""
        # Count source files (py, js, ts, go, rs, java)
        extensions = ("*.py", "*.js", "*.ts", "*.go", "*.rs", "*.java")
        source_count = 0
        test_count = 0

        for ext in extensions:
            output = self._run_git(
                "ls-files", "--", ext
            )
            if not output:
                continue
            files = output.splitlines()
            for f in files:
                basename = os.path.basename(f).lower()
                if basename.startswith("test_") or basename.endswith(
                    ("_test.py", "_test.js", "_test.ts", "_test.go",
                     ".test.js", ".test.ts", ".spec.js", ".spec.ts")
                ):
                    test_count += 1
                else:
                    source_count += 1

        # Test ratio score (1:1 = 100, 0 = 0)
        if source_count == 0:
            ratio_score = 50.0  # No source files - neutral
        else:
            ratio = test_count / source_count
            ratio_score = clamp_score(ratio * 100.0)

        # Check for lint config presence (adds up to 15 points)
        lint_configs = [
            ".eslintrc", ".eslintrc.js", ".eslintrc.json",
            "pyproject.toml", "setup.cfg", ".flake8",
            ".pylintrc", "tslint.json", ".prettierrc",
        ]
        lint_present = any(
            os.path.exists(os.path.join(self.repo_path, cfg))
            for cfg in lint_configs
        )
        lint_bonus = 15.0 if lint_present else 0.0

        # Weighted: 85% ratio, 15% lint config
        score = clamp_score(ratio_score * 0.85 + lint_bonus)

        return DimensionScore(
            name="code_quality",
            score=round(score, 1),
            grade=score_to_grade(score),
            details={
                "source_files": source_count,
                "test_files": test_count,
                "test_ratio": round(test_count / max(source_count, 1), 2),
                "lint_config_present": lint_present,
            },
        )

    def _score_velocity(self) -> DimensionScore:
        """Score velocity based on commit frequency and PR throughput."""
        # Commits in last 30 days
        since = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
        log_output = self._run_git(
            "log", "--oneline", f"--since={since}", "--no-merges"
        )
        commit_count = len(log_output.splitlines()) if log_output else 0
        commits_per_day = commit_count / 30.0

        # PRs merged in last 30 days via gh
        pr_output = self._run_gh(
            "pr", "list", "--state", "merged",
            "--search", f"merged:>={since}",
            "--json", "number", "--limit", "100"
        )
        try:
            prs = json.loads(pr_output) if pr_output else []
            pr_count = len(prs)
        except (json.JSONDecodeError, TypeError):
            pr_count = 0
        prs_per_week = pr_count / 4.3  # ~4.3 weeks in 30 days

        # Issues closed in last 30 days
        issue_output = self._run_gh(
            "issue", "list", "--state", "closed",
            "--search", f"closed:>={since}",
            "--json", "number", "--limit", "100"
        )
        try:
            issues = json.loads(issue_output) if issue_output else []
            issues_closed = len(issues)
        except (json.JSONDecodeError, TypeError):
            issues_closed = 0

        # Score components (each 0-100)
        # 2+ commits/day = 100, 0 = 0
        commit_score = clamp_score(commits_per_day * 50.0)
        # 3+ PRs/week = 100, 0 = 0
        pr_score = clamp_score(prs_per_week * 33.3)
        # 5+ issues/month = 100, 0 = 0
        issue_score = clamp_score(issues_closed * 20.0 / max(1, 5))

        score = clamp_score(
            commit_score * 0.4 + pr_score * 0.35 + issue_score * 0.25
        )

        return DimensionScore(
            name="velocity",
            score=round(score, 1),
            grade=score_to_grade(score),
            details={
                "commits_30d": commit_count,
                "commits_per_day": round(commits_per_day, 2),
                "prs_merged_30d": pr_count,
                "prs_per_week": round(prs_per_week, 2),
                "issues_closed_30d": issues_closed,
            },
        )

    def _score_debt(self) -> DimensionScore:
        """Score technical debt (inverted - lower debt = higher score)."""
        # Open bugs
        bug_output = self._run_gh(
            "issue", "list", "--state", "open",
            "--label", "bug",
            "--json", "number", "--limit", "100"
        )
        try:
            bugs = json.loads(bug_output) if bug_output else []
            open_bugs = len(bugs)
        except (json.JSONDecodeError, TypeError):
            open_bugs = 0

        # Stale PRs (open > 30 days)
        stale_pr_output = self._run_gh(
            "pr", "list", "--state", "open",
            "--json", "number,createdAt", "--limit", "100"
        )
        stale_prs = 0
        try:
            prs = json.loads(stale_pr_output) if stale_pr_output else []
            cutoff = (datetime.now() - timedelta(days=30)).isoformat()
            stale_prs = sum(
                1 for pr in prs
                if pr.get("createdAt", "") < cutoff
            )
        except (json.JSONDecodeError, TypeError):
            pass

        # TODO/FIXME density
        todo_output = self._run_git(
            "grep", "-c", "-i", "-E", r"TODO|FIXME|HACK|XXX", "--",
            "*.py", "*.js", "*.ts", "*.go", "*.rs", "*.java"
        )
        todo_count = 0
        if todo_output:
            for line in todo_output.splitlines():
                parts = line.rsplit(":", 1)
                if len(parts) == 2:
                    try:
                        todo_count += int(parts[1])
                    except ValueError:
                        pass

        # Inverted scoring (0 debt = 100, high debt = 0)
        # 0 bugs = 100, 10+ bugs = 0
        bug_score = clamp_score(100.0 - open_bugs * 10.0)
        # 0 stale PRs = 100, 5+ = 0
        stale_score = clamp_score(100.0 - stale_prs * 20.0)
        # 0 TODOs = 100, 50+ = 0
        todo_score = clamp_score(100.0 - todo_count * 2.0)

        score = clamp_score(
            bug_score * 0.4 + stale_score * 0.3 + todo_score * 0.3
        )

        return DimensionScore(
            name="debt",
            score=round(score, 1),
            grade=score_to_grade(score),
            details={
                "open_bugs": open_bugs,
                "stale_prs": stale_prs,
                "todo_fixme_count": todo_count,
            },
        )

    def _score_documentation(self) -> DimensionScore:
        """Score documentation coverage and freshness."""
        # Count doc files
        doc_extensions = ("*.md", "*.rst", "*.txt")
        doc_count = 0
        for ext in doc_extensions:
            output = self._run_git("ls-files", "--", ext)
            if output:
                doc_count += len(output.splitlines())

        # Count total tracked files
        all_files_output = self._run_git("ls-files")
        total_files = len(all_files_output.splitlines()) if all_files_output else 0

        # Doc ratio
        doc_ratio = doc_count / max(total_files, 1)
        # 10%+ docs = 100
        ratio_score = clamp_score(doc_ratio * 1000.0)

        # README freshness (days since last README change)
        readme_log = self._run_git(
            "log", "-1", "--format=%ct", "--", "README.md", "README.rst",
            "README.txt", "README"
        )
        readme_freshness_score = 0.0
        readme_days_old = -1
        if readme_log:
            try:
                last_modified = int(readme_log.splitlines()[0])
                days_old = (time.time() - last_modified) / 86400.0
                readme_days_old = round(days_old, 1)
                # 0 days = 100, 90+ days = 0
                readme_freshness_score = clamp_score(
                    100.0 - (days_old / 90.0) * 100.0
                )
            except (ValueError, IndexError):
                pass

        # README exists?
        readme_exists = os.path.exists(
            os.path.join(self.repo_path, "README.md")
        ) or os.path.exists(
            os.path.join(self.repo_path, "README.rst")
        )
        # 40% ratio + 35% freshness + 25% README existence
        # (Sprint 1 devil's advocate #7: removed duplicate clamp_score call that was silently overwritten.)
        score = clamp_score(
            ratio_score * 0.4
            + readme_freshness_score * 0.35
            + (100.0 if readme_exists else 0.0) * 0.25
        )

        return DimensionScore(
            name="documentation",
            score=round(score, 1),
            grade=score_to_grade(score),
            details={
                "doc_files": doc_count,
                "total_files": total_files,
                "doc_ratio": round(doc_ratio, 4),
                "readme_exists": readme_exists,
                "readme_days_old": readme_days_old,
            },
        )

    def _score_security(self) -> DimensionScore:
        """Score security posture: secrets scan, dependency age."""
        # Check for .env in tracked files (BAD)
        env_tracked = self._run_git("ls-files", "--", ".env", "*.env")
        env_in_repo = bool(env_tracked)

        # Check for .gitignore with .env exclusion (GOOD)
        gitignore_path = os.path.join(self.repo_path, ".gitignore")
        gitignore_has_env = False
        if os.path.exists(gitignore_path):
            try:
                with open(gitignore_path, "r") as f:
                    content = f.read()
                    gitignore_has_env = ".env" in content
            except OSError:
                pass

        # Check for security scanning config
        security_configs = [
            ".gitleaks.toml", ".trivyignore", ".snyk",
            "dependabot.yml", ".github/dependabot.yml",
        ]
        has_security_tool = any(
            os.path.exists(os.path.join(self.repo_path, cfg))
            for cfg in security_configs
        )

        # Dependency file freshness
        dep_files = [
            "package-lock.json", "yarn.lock", "Pipfile.lock",
            "poetry.lock", "go.sum", "Cargo.lock",
        ]
        newest_dep_days = -1
        for dep in dep_files:
            dep_log = self._run_git(
                "log", "-1", "--format=%ct", "--", dep
            )
            if dep_log:
                try:
                    ts = int(dep_log.splitlines()[0])
                    days = (time.time() - ts) / 86400.0
                    if newest_dep_days < 0 or days < newest_dep_days:
                        newest_dep_days = round(days, 1)
                except (ValueError, IndexError):
                    pass

        # Scoring
        # No .env in repo = 30 points
        env_score = 0.0 if env_in_repo else 100.0
        # .gitignore covers .env = 20 points
        gitignore_score = 100.0 if gitignore_has_env else 0.0
        # Security tool present = 20 points
        tool_score = 100.0 if has_security_tool else 0.0
        # Dependencies fresh (0 days = 100, 90+ = 0)
        if newest_dep_days >= 0:
            dep_score = clamp_score(100.0 - (newest_dep_days / 90.0) * 100.0)
        else:
            dep_score = 50.0  # No lock files - neutral

        score = clamp_score(
            env_score * 0.30
            + gitignore_score * 0.20
            + tool_score * 0.20
            + dep_score * 0.30
        )

        return DimensionScore(
            name="security",
            score=round(score, 1),
            grade=score_to_grade(score),
            details={
                "env_in_repo": env_in_repo,
                "gitignore_covers_env": gitignore_has_env,
                "has_security_tool": has_security_tool,
                "newest_dep_update_days": newest_dep_days,
            },
        )

    def _score_team_health(self) -> DimensionScore:
        """Score team health: contributor diversity, review turnaround."""
        # Contributors in last 90 days
        since_90 = (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%d")
        shortlog = self._run_git(
            "shortlog", "-sn", "--no-merges", f"--since={since_90}"
        )
        contributors = len(shortlog.splitlines()) if shortlog else 0

        # Average PR review turnaround (hours)
        review_output = self._run_gh(
            "pr", "list", "--state", "merged",
            "--json", "createdAt,mergedAt", "--limit", "20"
        )
        avg_turnaround_hours = -1.0
        try:
            prs = json.loads(review_output) if review_output else []
            turnarounds = []
            for pr in prs:
                created = pr.get("createdAt", "")
                merged = pr.get("mergedAt", "")
                if created and merged:
                    # Simple ISO parse (good enough for scoring)
                    c = datetime.fromisoformat(created.replace("Z", "+00:00"))
                    m = datetime.fromisoformat(merged.replace("Z", "+00:00"))
                    hours = (m - c).total_seconds() / 3600.0
                    if hours >= 0:
                        turnarounds.append(hours)
            if turnarounds:
                avg_turnaround_hours = round(
                    sum(turnarounds) / len(turnarounds), 1
                )
        except (json.JSONDecodeError, TypeError, ValueError):
            pass

        # Contributor diversity score
        # 1 = 30, 2 = 60, 3+ = 100
        if contributors >= 3:
            contrib_score = 100.0
        elif contributors == 2:
            contrib_score = 60.0
        elif contributors == 1:
            contrib_score = 30.0
        else:
            contrib_score = 0.0

        # Review turnaround score
        # <24h = 100, 24-48h = 70, 48-72h = 40, >72h = 10
        if avg_turnaround_hours < 0:
            review_score = 50.0  # No data
        elif avg_turnaround_hours <= 24:
            review_score = 100.0
        elif avg_turnaround_hours <= 48:
            review_score = 70.0
        elif avg_turnaround_hours <= 72:
            review_score = 40.0
        else:
            review_score = 10.0

        score = clamp_score(contrib_score * 0.5 + review_score * 0.5)

        return DimensionScore(
            name="team_health",
            score=round(score, 1),
            grade=score_to_grade(score),
            details={
                "contributors_90d": contributors,
                "avg_review_turnaround_hours": avg_turnaround_hours,
            },
        )

    def _score_innovation(self) -> DimensionScore:
        """Score innovation: feature-to-fix ratio, new file creation rate."""
        since_30 = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")

        # Feature vs fix commits (by conventional commit prefix)
        log_output = self._run_git(
            "log", "--oneline", "--no-merges", f"--since={since_30}"
        )
        feature_count = 0
        fix_count = 0
        other_count = 0
        if log_output:
            for line in log_output.splitlines():
                lower = line.lower()
                if "feat:" in lower or "feat(" in lower:
                    feature_count += 1
                elif "fix:" in lower or "fix(" in lower:
                    fix_count += 1
                else:
                    other_count += 1

        total_typed = feature_count + fix_count
        if total_typed == 0:
            feat_fix_ratio = 0.5  # Neutral when no typed commits
        else:
            feat_fix_ratio = feature_count / total_typed

        # New files created in last 30 days
        diff_output = self._run_git(
            "diff", "--diff-filter=A", "--name-only",
            f"--since={since_30}", "HEAD"
        )
        # Fallback: use log with numstat
        numstat = self._run_git(
            "log", "--diff-filter=A", "--name-only",
            "--pretty=format:", f"--since={since_30}"
        )
        new_files = 0
        if numstat:
            new_files = len([
                line for line in numstat.splitlines() if line.strip()
            ])

        # Feature-fix ratio score (0.5 = neutral 50, 1.0 = 100, 0.0 = 0)
        ratio_score = clamp_score(feat_fix_ratio * 100.0)

        # New file creation rate (10+ files/month = 100)
        creation_score = clamp_score(new_files * 10.0)

        score = clamp_score(ratio_score * 0.6 + creation_score * 0.4)

        return DimensionScore(
            name="innovation",
            score=round(score, 1),
            grade=score_to_grade(score),
            details={
                "feature_commits": feature_count,
                "fix_commits": fix_count,
                "other_commits": other_count,
                "feat_fix_ratio": round(feat_fix_ratio, 2),
                "new_files_30d": new_files,
            },
        )

    # --- Public API ---

    def calculate_score(self) -> float:
        """Calculate the overall weighted score across all dimensions.

        Returns:
            Overall score 0-100.
        """
        scorers = {
            "code_quality": self._score_code_quality,
            "velocity": self._score_velocity,
            "debt": self._score_debt,
            "documentation": self._score_documentation,
            "security": self._score_security,
            "team_health": self._score_team_health,
            "innovation": self._score_innovation,
        }

        self._dimensions = {}
        weighted_sum = 0.0

        for dim_name, scorer in scorers.items():
            dim_score = scorer()
            self._dimensions[dim_name] = dim_score
            weight = self.weights.get(dim_name, 0.0)
            weighted_sum += dim_score.score * weight

        return round(clamp_score(weighted_sum), 1)

    def get_letter_grade(self, score: Optional[float] = None) -> str:
        """Get letter grade for a score.

        Args:
            score: Score to grade. If None, calculates overall score.

        Returns:
            Letter grade string (A+ through F).
        """
        if score is None:
            score = self.calculate_score()
        return score_to_grade(score)

    def get_dimensions(self) -> dict[str, DimensionScore]:
        """Get all dimension scores. Calculates if not already done."""
        if not self._dimensions:
            self.calculate_score()
        return dict(self._dimensions)

    def get_trends(self, days: int = 30) -> dict:
        """Show score trends over time from saved history.

        Args:
            days: Number of days to look back.

        Returns:
            Dict with trend data including direction and delta.
        """
        history = self.load_history()
        if not history:
            return {
                "period_days": days,
                "snapshots": 0,
                "trend": "no_data",
                "delta": 0.0,
                "history": [],
            }

        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        recent = [
            s for s in history
            if s.get("timestamp", "") >= cutoff
        ]

        if len(recent) < 2:
            return {
                "period_days": days,
                "snapshots": len(recent),
                "trend": "insufficient_data",
                "delta": 0.0,
                "history": recent,
            }

        first_score = recent[0].get("overall_score", 0)
        last_score = recent[-1].get("overall_score", 0)
        delta = round(last_score - first_score, 1)

        if delta > 2.0:
            trend = "improving"
        elif delta < -2.0:
            trend = "declining"
        else:
            trend = "stable"

        # Per-dimension trends
        dim_trends = {}
        for dim in DEFAULT_WEIGHTS:
            first_dim = recent[0].get("dimensions", {}).get(dim, 0)
            last_dim = recent[-1].get("dimensions", {}).get(dim, 0)
            dim_delta = round(last_dim - first_dim, 1)
            if dim_delta > 2.0:
                dim_trend = "improving"
            elif dim_delta < -2.0:
                dim_trend = "declining"
            else:
                dim_trend = "stable"
            dim_trends[dim] = {
                "first": first_dim,
                "last": last_dim,
                "delta": dim_delta,
                "trend": dim_trend,
            }

        return {
            "period_days": days,
            "snapshots": len(recent),
            "trend": trend,
            "delta": delta,
            "first_score": first_score,
            "last_score": last_score,
            "dimension_trends": dim_trends,
            "history": recent,
        }

    def format_report(self) -> str:
        """Generate a detailed text report with recommendations.

        Returns:
            Multi-line string report.
        """
        if not self._dimensions:
            self.calculate_score()

        overall = sum(
            d.score * self.weights[name]
            for name, d in self._dimensions.items()
        )
        overall = round(clamp_score(overall), 1)
        grade = score_to_grade(overall)

        lines = [
            "=" * 60,
            "PROJECT HEALTH SCORECARD",
            "=" * 60,
            f"Repository: {self.repo_path}",
            f"Generated:  {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            f"Overall:    {overall}/100 ({grade})",
            "-" * 60,
        ]

        # Dimension details
        for dim_name in DEFAULT_WEIGHTS:
            dim = self._dimensions.get(dim_name)
            if not dim:
                continue
            label = dim_name.replace("_", " ").title()
            bar = self._score_bar(dim.score)
            lines.append(
                f"  {label:<20} {dim.score:>5.1f} ({dim.grade}) {bar}"
            )
            # Details
            for key, val in dim.details.items():
                lines.append(f"    {key}: {val}")

        # Recommendations for low-scoring dimensions
        low_dims = [
            (name, d) for name, d in self._dimensions.items()
            if d.score < 70
        ]
        if low_dims:
            lines.append("")
            lines.append("-" * 60)
            lines.append("RECOMMENDATIONS")
            lines.append("-" * 60)
            for name, d in sorted(low_dims, key=lambda x: x[1].score):
                label = name.replace("_", " ").title()
                lines.append(f"  {label} ({d.score:.1f}, {d.grade}):")
                recs = RECOMMENDATIONS.get(name, [])
                for rec in recs:
                    lines.append(f"    - {rec}")

        lines.append("=" * 60)
        return "\n".join(lines)

    def _score_bar(self, score: float, width: int = 20) -> str:
        """Generate a text progress bar for a score."""
        filled = int(score / 100.0 * width)
        return "[" + "#" * filled + "." * (width - filled) + "]"

    def format_slack_message(self) -> str:
        """Generate a compact Slack mrkdwn summary.

        Uses *bold* (not **bold**) per Slack mrkdwn spec.

        Returns:
            Slack mrkdwn formatted string.
        """
        if not self._dimensions:
            self.calculate_score()

        overall = sum(
            d.score * self.weights[name]
            for name, d in self._dimensions.items()
        )
        overall = round(clamp_score(overall), 1)
        grade = score_to_grade(overall)

        lines = [
            f"*Project Health Scorecard* - {grade} ({overall}/100)",
            "",
        ]

        for dim_name in DEFAULT_WEIGHTS:
            dim = self._dimensions.get(dim_name)
            if not dim:
                continue
            label = dim_name.replace("_", " ").title()
            emoji = self._grade_emoji(dim.grade)
            lines.append(f"{emoji} *{label}*: {dim.score:.0f} ({dim.grade})")

        # Add quick wins
        low_dims = [
            (name, d) for name, d in self._dimensions.items()
            if d.score < 70
        ]
        if low_dims:
            lines.append("")
            lines.append("*Quick Wins:*")
            for name, d in sorted(low_dims, key=lambda x: x[1].score)[:3]:
                recs = RECOMMENDATIONS.get(name, [])
                if recs:
                    lines.append(f"  - {recs[0]}")

        return "\n".join(lines)

    def _grade_emoji(self, grade: str) -> str:
        """Return a text indicator for a grade (no unicode emoji)."""
        if grade.startswith("A"):
            return "[++]"
        elif grade.startswith("B"):
            return "[+ ]"
        elif grade.startswith("C"):
            return "[  ]"
        elif grade.startswith("D"):
            return "[- ]"
        return "[--]"

    def generate_mcp_data(self) -> dict:
        """Return dict for grip_scoring_rubric MCP app.

        Returns:
            Structured dict with all scorecard data.
        """
        if not self._dimensions:
            self.calculate_score()

        overall = sum(
            d.score * self.weights[name]
            for name, d in self._dimensions.items()
        )
        overall = round(clamp_score(overall), 1)

        dimensions_data = {}
        for name, dim in self._dimensions.items():
            dimensions_data[name] = {
                "score": dim.score,
                "grade": dim.grade,
                "weight": self.weights[name],
                "weighted_contribution": round(
                    dim.score * self.weights[name], 2
                ),
                "details": dim.details,
            }

        return {
            "type": "project_scorecard",
            "version": "1.0.0",
            "timestamp": datetime.now().isoformat(),
            "repo_path": self.repo_path,
            "overall": {
                "score": overall,
                "grade": score_to_grade(overall),
            },
            "dimensions": dimensions_data,
            "weights": self.weights,
        }

    def generate_gap_analysis(self) -> dict:
        """Return dict for grip_gap_visualiser MCP app.

        Shows current vs target scores with gap magnitude.

        Returns:
            Dict with gap analysis data per dimension.
        """
        if not self._dimensions:
            self.calculate_score()

        overall = sum(
            d.score * self.weights[name]
            for name, d in self._dimensions.items()
        )
        overall = round(clamp_score(overall), 1)

        gaps = {}
        for name, dim in self._dimensions.items():
            target = self.targets.get(name, 85.0)
            gap = round(target - dim.score, 1)
            gaps[name] = {
                "current": dim.score,
                "target": target,
                "gap": gap,
                "on_target": gap <= 0,
                "priority": self._gap_priority(gap),
                "recommendations": (
                    RECOMMENDATIONS.get(name, []) if gap > 0 else []
                ),
            }

        overall_target = sum(
            self.targets.get(dim, 85.0) * self.weights[dim]
            for dim in DEFAULT_WEIGHTS
        )
        overall_gap = round(overall_target - overall, 1)

        return {
            "type": "gap_analysis",
            "version": "1.0.0",
            "timestamp": datetime.now().isoformat(),
            "repo_path": self.repo_path,
            "overall": {
                "current": overall,
                "target": round(overall_target, 1),
                "gap": overall_gap,
                "on_target": overall_gap <= 0,
            },
            "dimensions": gaps,
            "priority_order": sorted(
                gaps.keys(),
                key=lambda k: gaps[k]["gap"],
                reverse=True,
            ),
        }

    def _gap_priority(self, gap: float) -> str:
        """Classify gap size into priority level."""
        if gap <= 0:
            return "on_target"
        elif gap <= 10:
            return "low"
        elif gap <= 25:
            return "medium"
        else:
            return "high"

    def save_snapshot(self) -> str:
        """Save current scores to JSONL history file.

        Returns:
            Path to the history file.
        """
        if not self._dimensions:
            self.calculate_score()

        overall = sum(
            d.score * self.weights[name]
            for name, d in self._dimensions.items()
        )
        overall = round(clamp_score(overall), 1)

        snapshot = ScorecardSnapshot(
            timestamp=datetime.now().isoformat(),
            overall_score=overall,
            overall_grade=score_to_grade(overall),
            dimensions={
                name: dim.score
                for name, dim in self._dimensions.items()
            },
            repo_path=self.repo_path,
            weights=self.weights,
        )

        # Append to JSONL
        history_dir = os.path.dirname(self.history_path)
        if history_dir and not os.path.exists(history_dir):
            os.makedirs(history_dir, exist_ok=True)

        with open(self.history_path, "a") as f:
            f.write(snapshot.to_json() + "\n")

        return self.history_path

    def load_history(self) -> list[dict]:
        """Load score history from JSONL file.

        Returns:
            List of snapshot dicts, sorted by timestamp.
        """
        if not os.path.exists(self.history_path):
            return []

        snapshots = []
        try:
            with open(self.history_path, "r") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            snapshots.append(json.loads(line))
                        except json.JSONDecodeError:
                            continue
        except OSError:
            return []

        return sorted(snapshots, key=lambda s: s.get("timestamp", ""))
