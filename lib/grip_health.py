#!/usr/bin/env python3
"""
grip_health.py -- GRIP system health aggregator.

Provides a unified view of all GRIP subsystems: memory, sessions, git,
tests, skills, agents, genome, and modes. Produces health scores,
structured dicts, pretty reports, and compact statusline summaries.

Usage:
    from lib.grip_health import GripHealth

    health = GripHealth()
    print(health.format_report())
    print(health.health_score())

CLI:
    python3 lib/grip_health.py              # full report
    python3 lib/grip_health.py --compact    # single-line statusline
    python3 lib/grip_health.py --json       # JSON output
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Resolve CLAUDE_DIR from env, defaulting to ~/.claude
CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", Path.home() / ".claude"))

# Subsystem status constants
STATUS_HEALTHY = "healthy"
STATUS_WARNING = "warning"
STATUS_ERROR = "error"

# Scoring weights
SCORE_MEMORY_FRESH = 15
SCORE_TESTS_PRESENT = 15
SCORE_NO_STALE_SESSIONS = 10
SCORE_GIT_CLEAN = 10
SCORE_SKILLS_THRESHOLD = 15
SCORE_RECENT_COMMITS = 10
SCORE_GENOME_ACTIVE = 10
SCORE_TODO_COMPLETION = 15

# Thresholds
MEMORY_FRESH_HOURS = 24
MEMORY_RECENT_HOURS = 168  # 1 week
STALE_SESSION_SECONDS = 300  # 5 minutes
SKILLS_HEALTHY_COUNT = 50
SECONDS_IN_DAY = 86400


@dataclass
class SubsystemResult:
    """Result of a single subsystem health check."""

    status: str = STATUS_HEALTHY
    details: Dict[str, Any] = field(default_factory=dict)
    score: int = 0


class GripHealth:
    """Aggregates health status from all GRIP subsystems."""

    def __init__(self, claude_dir: Optional[Path] = None) -> None:
        self.claude_dir = Path(claude_dir) if claude_dir else CLAUDE_DIR
        self._cache: Dict[str, SubsystemResult] = {}

    # -- Public API --

    def health_score(self) -> int:
        """Composite health score 0-100."""
        data = self.to_dict()
        total = 0
        for subsystem in data["subsystems"].values():
            total += subsystem.get("score", 0)
        return min(100, max(0, total))

    def to_dict(self) -> Dict[str, Any]:
        """Full health data as a JSON-serializable dict."""
        subsystems = {
            "memory": self._check_memory(),
            "sessions": self._check_sessions(),
            "git": self._check_git(),
            "tests": self._check_tests(),
            "skills": self._check_skills(),
            "agents": self._check_agents(),
            "genome": self._check_genome(),
            "modes": self._check_modes(),
        }
        # Convert SubsystemResult to dict
        result: Dict[str, Any] = {"subsystems": {}}
        total_score = 0
        for name, sub in subsystems.items():
            entry = {
                "status": sub.status,
                "details": sub.details,
                "score": sub.score,
            }
            result["subsystems"][name] = entry
            total_score += sub.score

        result["health_score"] = min(100, max(0, total_score))
        result["timestamp"] = datetime.now(timezone.utc).isoformat()
        return result

    def format_report(self) -> str:
        """Pretty text report with sections and status indicators."""
        data = self.to_dict()
        lines: List[str] = []
        lines.append("=" * 52)
        lines.append("  GRIP System Health Report")
        lines.append("=" * 52)
        lines.append("")

        indicator_map = {
            STATUS_HEALTHY: "[OK]",
            STATUS_WARNING: "[!!]",
            STATUS_ERROR: "[XX]",
        }

        for name, sub in data["subsystems"].items():
            indicator = indicator_map.get(sub["status"], "[??]")
            title = name.capitalize()
            lines.append(f"  {indicator} {title} (score: {sub['score']})")

            for key, value in sub["details"].items():
                display_key = key.replace("_", " ").capitalize()
                lines.append(f"       {display_key}: {value}")
            lines.append("")

        lines.append("-" * 52)
        score = data["health_score"]
        grade = _score_to_grade(score)
        lines.append(f"  Health Score: {score}/100 ({grade})")
        lines.append(f"  Timestamp: {data['timestamp']}")
        lines.append("=" * 52)
        return "\n".join(lines)

    def format_compact(self) -> str:
        """Single-line summary for statusline use."""
        data = self.to_dict()
        score = data["health_score"]
        grade = _score_to_grade(score)

        counts = {STATUS_HEALTHY: 0, STATUS_WARNING: 0, STATUS_ERROR: 0}
        for sub in data["subsystems"].values():
            status = sub["status"]
            if status in counts:
                counts[status] += 1

        parts = [f"GRIP:{score}/100({grade})"]
        if counts[STATUS_ERROR] > 0:
            parts.append(f"{counts[STATUS_ERROR]}err")
        if counts[STATUS_WARNING] > 0:
            parts.append(f"{counts[STATUS_WARNING]}warn")
        parts.append(f"{counts[STATUS_HEALTHY]}ok")
        return " ".join(parts)

    # -- Subsystem checks --

    def _check_memory(self) -> SubsystemResult:
        """Check MEMORY.md health: existence, line count, freshness, TODOs."""
        if "memory" in self._cache:
            return self._cache["memory"]

        result = SubsystemResult()
        memory_paths = [
            self.claude_dir / "projects" / "memory" / "MEMORY.md",
        ]
        # Also search for any project-level MEMORY.md
        projects_dir = self.claude_dir / "projects"
        memory_file: Optional[Path] = None

        try:
            if projects_dir.is_dir():
                for candidate in projects_dir.rglob("MEMORY.md"):
                    memory_file = candidate
                    break  # take first match
        except OSError:
            pass

        if memory_file is None:
            # Fallback: check top-level
            top_level = self.claude_dir / "MEMORY.md"
            if _safe_exists(top_level):
                memory_file = top_level

        if memory_file is None or not _safe_exists(memory_file):
            result.status = STATUS_ERROR
            result.details = {"exists": False, "line_count": 0}
            result.score = 0
            self._cache["memory"] = result
            return result

        # Read content
        try:
            content = memory_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            result.status = STATUS_ERROR
            result.details = {"exists": True, "readable": False}
            result.score = 5
            self._cache["memory"] = result
            return result

        lines = content.splitlines()
        line_count = len(lines)

        # Freshness
        mtime = _safe_mtime(memory_file)
        age_hours = _age_hours(mtime) if mtime else None

        # TODO counts
        pending = 0
        done = 0
        for line in lines:
            lower = line.lower()
            if "pending" in lower:
                pending += 1
            if "done" in lower or "learned" in lower or "fixed" in lower:
                done += 1

        result.details = {
            "exists": True,
            "path": str(memory_file),
            "line_count": line_count,
            "last_modified_hours_ago": round(age_hours, 1) if age_hours is not None else None,
            "todos_pending": pending,
            "todos_done": done,
        }

        # Score: freshness-based
        if age_hours is not None and age_hours <= MEMORY_FRESH_HOURS:
            result.score = SCORE_MEMORY_FRESH
            result.status = STATUS_HEALTHY
        elif age_hours is not None and age_hours <= MEMORY_RECENT_HOURS:
            result.score = 10
            result.status = STATUS_HEALTHY
        else:
            result.score = 5
            result.status = STATUS_WARNING

        self._cache["memory"] = result
        return result

    def _check_sessions(self) -> SubsystemResult:
        """Count active JSONL session files and detect stale sessions."""
        if "sessions" in self._cache:
            return self._cache["sessions"]

        result = SubsystemResult()
        projects_dir = self.claude_dir / "projects"
        jsonl_files: List[Path] = []

        try:
            if projects_dir.is_dir():
                for f in projects_dir.rglob("*.jsonl"):
                    jsonl_files.append(f)
        except OSError:
            pass

        now = time.time()
        stale_count = 0
        total_conversations = len(jsonl_files)

        for f in jsonl_files:
            mtime = _safe_mtime(f)
            if mtime and (now - mtime) < STALE_SESSION_SECONDS:
                stale_count += 1

        # Active = modified in last 5 minutes (same as stale threshold --
        # "active" means recently touched, "stale" means touched but possibly abandoned)
        active_count = stale_count  # recently modified files
        # For scoring, "stale" means sessions that are old but not cleaned up
        # Redefine: stale = files older than 5 min but modified within 1 hour
        stale_count = 0
        for f in jsonl_files:
            mtime = _safe_mtime(f)
            if mtime:
                age = now - mtime
                if STALE_SESSION_SECONDS < age < 3600:
                    stale_count += 1

        result.details = {
            "total_session_files": total_conversations,
            "active_sessions": active_count,
            "stale_sessions": stale_count,
        }

        if stale_count == 0:
            result.score = SCORE_NO_STALE_SESSIONS
            result.status = STATUS_HEALTHY
        elif stale_count <= 3:
            result.score = 5
            result.status = STATUS_WARNING
        else:
            result.score = 0
            result.status = STATUS_ERROR

        self._cache["sessions"] = result
        return result

    def _check_git(self) -> SubsystemResult:
        """Check git status: branch, ahead/behind, uncommitted changes, recent commits."""
        if "git" in self._cache:
            return self._cache["git"]

        result = SubsystemResult()

        if not _safe_exists(self.claude_dir / ".git"):
            result.status = STATUS_WARNING
            result.details = {"is_git_repo": False}
            result.score = 0
            self._cache["git"] = result
            return result

        branch = _git_cmd(self.claude_dir, ["rev-parse", "--abbrev-ref", "HEAD"])
        uncommitted = _git_cmd(self.claude_dir, ["status", "--porcelain"])
        uncommitted_count = len(uncommitted.strip().splitlines()) if uncommitted.strip() else 0

        # Recent commits in last 24h
        since = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
        recent_commits_output = _git_cmd(
            self.claude_dir,
            ["log", "--oneline", "--since=24 hours ago"],
        )
        recent_count = len(recent_commits_output.strip().splitlines()) if recent_commits_output.strip() else 0

        # Ahead/behind
        ahead = 0
        behind = 0
        ab_output = _git_cmd(
            self.claude_dir,
            ["rev-list", "--left-right", "--count", f"{branch}...@{{upstream}}"],
        )
        if ab_output and "\t" in ab_output:
            parts = ab_output.strip().split("\t")
            if len(parts) == 2:
                try:
                    ahead = int(parts[0])
                    behind = int(parts[1])
                except ValueError:
                    pass

        result.details = {
            "is_git_repo": True,
            "branch": branch or "unknown",
            "uncommitted_changes": uncommitted_count,
            "ahead": ahead,
            "behind": behind,
            "recent_commits_24h": recent_count,
        }

        # Score: clean working tree
        if uncommitted_count == 0:
            result.score = SCORE_GIT_CLEAN
            result.status = STATUS_HEALTHY
        elif uncommitted_count <= 10:
            result.score = 5
            result.status = STATUS_WARNING
        else:
            result.score = 0
            result.status = STATUS_WARNING

        # Bonus for recent commits
        if recent_count > 0:
            result.score = min(result.score + SCORE_RECENT_COMMITS, SCORE_GIT_CLEAN + SCORE_RECENT_COMMITS)

        self._cache["git"] = result
        return result

    def _check_tests(self) -> SubsystemResult:
        """Check test infrastructure: pytest cache, test file count, test estimate."""
        if "tests" in self._cache:
            return self._cache["tests"]

        result = SubsystemResult()
        tests_dir = self.claude_dir / "tests"

        test_files: List[Path] = []
        try:
            if tests_dir.is_dir():
                for f in tests_dir.iterdir():
                    if f.is_file() and f.name.startswith("test_") and f.suffix == ".py":
                        test_files.append(f)
        except OSError:
            pass

        test_file_count = len(test_files)

        # Estimate total test count by scanning for 'def test_' patterns
        test_count_estimate = 0
        for tf in test_files:
            try:
                content = tf.read_text(encoding="utf-8", errors="replace")
                test_count_estimate += content.count("def test_")
            except OSError:
                pass

        # Check for pytest cache (indicates tests have been run)
        pytest_cache = self.claude_dir / ".pytest_cache"
        lastfailed_path = pytest_cache / "v" / "cache" / "lastfailed"
        has_pytest_cache = _safe_exists(pytest_cache)

        last_run_status: Optional[str] = None
        if _safe_exists(lastfailed_path):
            try:
                content = lastfailed_path.read_text(encoding="utf-8", errors="replace")
                data = json.loads(content)
                if not data:
                    last_run_status = "all_passed"
                else:
                    last_run_status = f"{len(data)}_failures"
            except (OSError, json.JSONDecodeError):
                last_run_status = "unknown"
        elif has_pytest_cache:
            last_run_status = "cache_present"

        result.details = {
            "test_file_count": test_file_count,
            "test_count_estimate": test_count_estimate,
            "has_pytest_cache": has_pytest_cache,
            "last_run_status": last_run_status,
        }

        if test_file_count > 0:
            result.score = SCORE_TESTS_PRESENT
            result.status = STATUS_HEALTHY
        else:
            result.score = 0
            result.status = STATUS_ERROR

        self._cache["tests"] = result
        return result

    def _check_skills(self) -> SubsystemResult:
        """Count skill directories and check active/inactive status."""
        if "skills" in self._cache:
            return self._cache["skills"]

        result = SubsystemResult()
        skills_dir = self.claude_dir / "skills"

        total = 0
        archived = 0
        active = 0

        try:
            if skills_dir.is_dir():
                for entry in skills_dir.iterdir():
                    if entry.is_dir():
                        if entry.name.startswith("_"):
                            archived += 1
                        else:
                            active += 1
                        total += 1
        except OSError:
            pass

        result.details = {
            "total_skills": total,
            "active_skills": active,
            "archived_skills": archived,
        }

        if active >= SKILLS_HEALTHY_COUNT:
            result.score = SCORE_SKILLS_THRESHOLD
            result.status = STATUS_HEALTHY
        elif active > 0:
            result.score = round(SCORE_SKILLS_THRESHOLD * (active / SKILLS_HEALTHY_COUNT))
            result.status = STATUS_WARNING
        else:
            result.score = 0
            result.status = STATUS_ERROR

        self._cache["skills"] = result
        return result

    def _check_agents(self) -> SubsystemResult:
        """Count agent types available."""
        if "agents" in self._cache:
            return self._cache["agents"]

        result = SubsystemResult()
        agents_dir = self.claude_dir / "agents"

        agent_count = 0
        try:
            if agents_dir.is_dir():
                for entry in agents_dir.iterdir():
                    if entry.is_file() or entry.is_dir():
                        agent_count += 1
        except OSError:
            pass

        result.details = {"agent_count": agent_count}

        if agent_count > 0:
            result.score = 5  # Agents contribute but don't have a dedicated weight
            result.status = STATUS_HEALTHY
        else:
            result.score = 0
            result.status = STATUS_WARNING

        self._cache["agents"] = result
        return result

    def _check_genome(self) -> SubsystemResult:
        """Check genome status: existence, principle count."""
        if "genome" in self._cache:
            return self._cache["genome"]

        result = SubsystemResult()
        genome_path = self.claude_dir / "cache" / "genome.json"

        if not _safe_exists(genome_path):
            result.status = STATUS_WARNING
            result.details = {"exists": False, "path": str(genome_path)}
            result.score = 0
            self._cache["genome"] = result
            return result

        try:
            content = genome_path.read_text(encoding="utf-8", errors="replace")
            data = json.loads(content)
        except (OSError, json.JSONDecodeError):
            result.status = STATUS_ERROR
            result.details = {"exists": True, "readable": False}
            result.score = 0
            self._cache["genome"] = result
            return result

        # Count principles/genes
        genes = data.get("genes", [])
        principle_count = len(genes)
        generation = data.get("generation", 0)

        result.details = {
            "exists": True,
            "path": str(genome_path),
            "principle_count": principle_count,
            "generation": generation,
        }
        result.score = SCORE_GENOME_ACTIVE
        result.status = STATUS_HEALTHY

        self._cache["genome"] = result
        return result

    def _check_modes(self) -> SubsystemResult:
        """Count mode YAML definition files."""
        if "modes" in self._cache:
            return self._cache["modes"]

        result = SubsystemResult()
        modes_dir = self.claude_dir / "modes" / "definitions"

        mode_count = 0
        try:
            if modes_dir.is_dir():
                for entry in modes_dir.iterdir():
                    if entry.is_file() and entry.suffix in (".yaml", ".yml"):
                        mode_count += 1
        except OSError:
            pass

        result.details = {"mode_count": mode_count}

        if mode_count > 0:
            result.score = 5  # Modes contribute but don't have a dedicated weight
            result.status = STATUS_HEALTHY
        else:
            result.score = 0
            result.status = STATUS_WARNING

        self._cache["modes"] = result
        return result

    # -- Internal cache management --

    def clear_cache(self) -> None:
        """Clear cached subsystem results for re-evaluation."""
        self._cache.clear()


# -- Helpers --


def _safe_exists(path: Path) -> bool:
    """Check if path exists, catching permission errors."""
    try:
        return path.exists()
    except OSError:
        return False


def _safe_mtime(path: Path) -> Optional[float]:
    """Get modification time, returning None on failure."""
    try:
        return path.stat().st_mtime
    except OSError:
        return None


def _age_hours(mtime: float) -> float:
    """Hours since a given mtime."""
    return (time.time() - mtime) / 3600.0


def _git_cmd(repo_dir: Path, args: List[str]) -> str:
    """Run a git command, returning stdout or empty string on failure."""
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo_dir)] + args,
            capture_output=True,
            text=True,
            timeout=10,
        )
        return proc.stdout.strip() if proc.returncode == 0 else ""
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return ""


def _score_to_grade(score: int) -> str:
    """Map a 0-100 score to a letter grade."""
    if score >= 90:
        return "A"
    if score >= 80:
        return "B"
    if score >= 70:
        return "C"
    if score >= 60:
        return "D"
    return "F"


# -- CLI --


def main() -> None:
    """CLI entry point."""
    import sys

    health = GripHealth()

    if "--json" in sys.argv:
        print(json.dumps(health.to_dict(), indent=2))
    elif "--compact" in sys.argv:
        print(health.format_compact())
    elif "--score" in sys.argv:
        print(health.health_score())
    else:
        print(health.format_report())


if __name__ == "__main__":
    main()
