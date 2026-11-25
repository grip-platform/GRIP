#!/usr/bin/env python3
"""GRIP Achievement System -- milestone badges and streak tracking.

Gamification layer that tracks GRIP usage history (sessions, converge runs,
genome evolutions, skills, RSI usage) and unlocks badges based on milestones.

Usage:
    python3 lib/achievements.py check    # Show all badges with unlock status
    python3 lib/achievements.py unlock   # Scan history, unlock earned badges
    python3 lib/achievements.py --json   # Machine-readable output
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class Badge:
    """A single achievement badge definition and its unlock state."""

    name: str
    description: str
    category: str  # explorer, builder, improver, scientist, meta
    icon: str  # text placeholder
    criteria: str
    unlocked_at: Optional[str] = None  # ISO datetime or None

    @property
    def is_unlocked(self) -> bool:
        return self.unlocked_at is not None

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# Badge catalogue -- 16 badges across 5 categories
# ---------------------------------------------------------------------------

BADGE_CATALOGUE: List[Dict[str, str]] = [
    # EXPLORER
    {
        "name": "First Session",
        "description": "Complete your first GRIP session",
        "category": "explorer",
        "icon": "[E1]",
        "criteria": "sessions >= 1",
    },
    {
        "name": "Deep Diver",
        "description": "Run a session with over 200 messages",
        "category": "explorer",
        "icon": "[E2]",
        "criteria": "max_session_messages > 200",
    },
    {
        "name": "Night Owl",
        "description": "Start a session after midnight",
        "category": "explorer",
        "icon": "[E3]",
        "criteria": "night_sessions >= 1",
    },
    # BUILDER
    {
        "name": "First Commit",
        "description": "Make your first commit during a GRIP session",
        "category": "builder",
        "icon": "[B1]",
        "criteria": "total_commits >= 1",
    },
    {
        "name": "PR Machine",
        "description": "Create 10 or more PRs in a single sprint",
        "category": "builder",
        "icon": "[B2]",
        "criteria": "max_prs_in_sprint >= 10",
    },
    {
        "name": "Test Writer",
        "description": "Create 50 or more tests across sessions",
        "category": "builder",
        "icon": "[B3]",
        "criteria": "tests_created >= 50",
    },
    # IMPROVER
    {
        "name": "First Converge",
        "description": "Run your first converge loop",
        "category": "improver",
        "icon": "[I1]",
        "criteria": "converge_runs >= 1",
    },
    {
        "name": "Convergence Master",
        "description": "Complete 10 converge runs",
        "category": "improver",
        "icon": "[I2]",
        "criteria": "converge_runs >= 10",
    },
    {
        "name": "Score Breaker",
        "description": "Achieve a converge score above 95",
        "category": "improver",
        "icon": "[I3]",
        "criteria": "max_converge_score > 95",
    },
    {
        "name": "Streak 5",
        "description": "Use GRIP for 5 consecutive days",
        "category": "improver",
        "icon": "[I4]",
        "criteria": "usage_streak >= 5",
    },
    # SCIENTIST
    {
        "name": "Gene Pioneer",
        "description": "Trigger your first genome evolution",
        "category": "scientist",
        "icon": "[S1]",
        "criteria": "genome_evolutions >= 1",
    },
    {
        "name": "Micro-Skill Genesis",
        "description": "Auto-generate your first learned skill",
        "category": "scientist",
        "icon": "[S2]",
        "criteria": "skills_generated >= 1",
    },
    {
        "name": "Pattern Hunter",
        "description": "Use the conversation patterns analyzer",
        "category": "scientist",
        "icon": "[S3]",
        "criteria": "pattern_analyses >= 1",
    },
    {
        "name": "Anomaly Spotter",
        "description": "Use the convergence anomaly detector",
        "category": "scientist",
        "icon": "[S4]",
        "criteria": "anomaly_analyses >= 1",
    },
    # META
    {
        "name": "Self-Improver",
        "description": "Run an RSI cycle",
        "category": "meta",
        "icon": "[M1]",
        "criteria": "rsi_runs >= 1",
    },
    {
        "name": "Achievement Hunter",
        "description": "Unlock 10 or more badges",
        "category": "meta",
        "icon": "[M2]",
        "criteria": "unlocked_count >= 10",
    },
]


def _make_badge(defn: Dict[str, str], unlocked_at: Optional[str] = None) -> Badge:
    """Create a Badge from a catalogue definition."""
    return Badge(
        name=defn["name"],
        description=defn["description"],
        category=defn["category"],
        icon=defn["icon"],
        criteria=defn["criteria"],
        unlocked_at=unlocked_at,
    )


# ---------------------------------------------------------------------------
# AchievementStore -- persistence
# ---------------------------------------------------------------------------

STORE_FILENAME = "achievements.json"


@dataclass
class AchievementStore:
    """Persisted achievement state."""

    unlocked: Dict[str, str] = field(default_factory=dict)
    # badge name -> ISO timestamp of unlock

    stats_snapshot: Dict[str, Any] = field(default_factory=dict)
    # last known stats used for unlock checks

    def to_dict(self) -> dict:
        return {"unlocked": self.unlocked, "stats_snapshot": self.stats_snapshot}

    @classmethod
    def from_dict(cls, data: dict) -> "AchievementStore":
        return cls(
            unlocked=data.get("unlocked", {}),
            stats_snapshot=data.get("stats_snapshot", {}),
        )


def load_store(cache_dir: str) -> AchievementStore:
    """Load achievement store from cache directory."""
    path = Path(cache_dir) / STORE_FILENAME
    if not path.exists():
        return AchievementStore()
    try:
        data = json.loads(path.read_text())
        return AchievementStore.from_dict(data)
    except (json.JSONDecodeError, KeyError, TypeError):
        return AchievementStore()


def save_store(store: AchievementStore, cache_dir: str) -> None:
    """Save achievement store to cache directory."""
    path = Path(cache_dir) / STORE_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(store.to_dict(), indent=2) + "\n")


# ---------------------------------------------------------------------------
# History scanners
# ---------------------------------------------------------------------------

def scan_sessions(projects_dir: str) -> Dict[str, Any]:
    """Extract session stats from GRIP project data.

    Scans JSONL session files in the projects directory to count sessions,
    message counts, and timing information.

    Returns dict with keys:
        sessions, max_session_messages, night_sessions, usage_dates,
        total_commits, max_prs_in_sprint, tests_created, rsi_runs,
        pattern_analyses, anomaly_analyses
    """
    stats: Dict[str, Any] = {
        "sessions": 0,
        "max_session_messages": 0,
        "night_sessions": 0,
        "usage_dates": [],
        "total_commits": 0,
        "max_prs_in_sprint": 0,
        "tests_created": 0,
        "rsi_runs": 0,
        "pattern_analyses": 0,
        "anomaly_analyses": 0,
    }

    if not os.path.isdir(projects_dir):
        return stats

    # Walk all project subdirectories for JSONL session files
    session_files = []
    for project_subdir in _list_dirs(projects_dir):
        project_path = os.path.join(projects_dir, project_subdir)
        for fname in os.listdir(project_path):
            if fname.endswith(".jsonl"):
                session_files.append(os.path.join(project_path, fname))

    stats["sessions"] = len(session_files)

    for session_file in session_files:
        msg_count = 0
        first_ts = None
        try:
            with open(session_file) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    msg_count += 1
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    # Extract timestamp from first message
                    if first_ts is None and isinstance(entry, dict):
                        ts = entry.get("timestamp")
                        if ts and isinstance(ts, str):
                            try:
                                first_ts = datetime.fromisoformat(
                                    ts.replace("Z", "+00:00")
                                )
                            except (ValueError, TypeError):
                                pass

                    # Count tool usage patterns
                    if isinstance(entry, dict):
                        msg_text = str(entry.get("snapshot", ""))
                        if "git commit" in msg_text or "git add" in msg_text:
                            stats["total_commits"] += 1
                        if "gh pr create" in msg_text:
                            stats["max_prs_in_sprint"] += 1
                        if "test_" in msg_text or "pytest" in msg_text:
                            stats["tests_created"] += 1
                        if "/rsi" in msg_text or "rsi_" in msg_text:
                            stats["rsi_runs"] += 1
                        if "conversation_patterns" in msg_text:
                            stats["pattern_analyses"] += 1
                        if "converge_anomaly" in msg_text:
                            stats["anomaly_analyses"] += 1

        except (OSError, PermissionError):
            continue

        stats["max_session_messages"] = max(
            stats["max_session_messages"], msg_count
        )

        if first_ts is not None:
            date_str = first_ts.strftime("%Y-%m-%d")
            if date_str not in stats["usage_dates"]:
                stats["usage_dates"].append(date_str)
            if first_ts.hour >= 0 and first_ts.hour < 5:
                stats["night_sessions"] += 1

    return stats


def scan_converge_history(learning_dir: str) -> Dict[str, Any]:
    """Extract converge stats from learning/converge data.

    Returns dict with keys:
        converge_runs, max_converge_score
    """
    stats: Dict[str, Any] = {
        "converge_runs": 0,
        "max_converge_score": 0.0,
    }

    if not os.path.isdir(learning_dir):
        return stats

    # Check converge results in learning directory
    for root, _dirs, files in os.walk(learning_dir):
        for fname in files:
            if not fname.endswith(".json"):
                continue
            fpath = os.path.join(root, fname)
            try:
                data = json.loads(Path(fpath).read_text())
            except (json.JSONDecodeError, OSError):
                continue

            if isinstance(data, dict):
                # Count converge-related entries
                if "converge" in fname.lower() or data.get("type") == "converge":
                    stats["converge_runs"] += 1
                score = data.get("score") or data.get("final_score") or 0
                if isinstance(score, (int, float)):
                    stats["max_converge_score"] = max(
                        stats["max_converge_score"], float(score)
                    )

    return stats


def scan_genome_history(cache_dir: str) -> Dict[str, int]:
    """Count genome evolutions from genome-history.jsonl.

    Returns dict with key: genome_evolutions
    """
    stats = {"genome_evolutions": 0}
    history_path = os.path.join(cache_dir, "genome-history.jsonl")
    if not os.path.exists(history_path):
        return stats
    try:
        with open(history_path) as f:
            for line in f:
                if line.strip():
                    stats["genome_evolutions"] += 1
    except OSError:
        pass
    return stats


def scan_skills(learning_dir: str) -> Dict[str, int]:
    """Count auto-generated skills.

    Returns dict with key: skills_generated
    """
    stats = {"skills_generated": 0}
    approved_dir = os.path.join(learning_dir, "approved")
    if os.path.isdir(approved_dir):
        stats["skills_generated"] = len(
            [f for f in os.listdir(approved_dir) if f.endswith(".json")]
        )
    processed_dir = os.path.join(learning_dir, "processed")
    if os.path.isdir(processed_dir):
        stats["skills_generated"] += len(
            [f for f in os.listdir(processed_dir) if f.endswith(".json")]
        )
    return stats


def _list_dirs(path: str) -> List[str]:
    """List subdirectories (non-recursive), ignoring errors."""
    try:
        return [
            d
            for d in os.listdir(path)
            if os.path.isdir(os.path.join(path, d))
        ]
    except OSError:
        return []


def compute_usage_streak(usage_dates: List[str]) -> int:
    """Compute longest consecutive usage streak from date strings."""
    if not usage_dates:
        return 0
    try:
        dates = sorted(set(datetime.strptime(d, "%Y-%m-%d").date() for d in usage_dates))
    except ValueError:
        return 0
    if not dates:
        return 0

    best = 1
    current = 1
    for i in range(1, len(dates)):
        if (dates[i] - dates[i - 1]).days == 1:
            current += 1
            best = max(best, current)
        else:
            current = 1
    return best


# ---------------------------------------------------------------------------
# Badge unlock logic
# ---------------------------------------------------------------------------

def _evaluate_criterion(criteria: str, stats: Dict[str, Any]) -> bool:
    """Evaluate a badge criterion against stats.

    Criteria format: "key >= N" or "key > N"
    """
    parts = criteria.split()
    if len(parts) != 3:
        return False

    key, op, threshold_str = parts
    try:
        threshold = float(threshold_str)
    except ValueError:
        return False

    value = stats.get(key, 0)
    if not isinstance(value, (int, float)):
        value = 0

    if op == ">=":
        return value >= threshold
    elif op == ">":
        return value > threshold
    elif op == "==":
        return value == threshold
    elif op == "<=":
        return value <= threshold
    elif op == "<":
        return value < threshold
    return False


def check_unlocks(store: AchievementStore, stats: Optional[Dict[str, Any]] = None) -> List[Badge]:
    """Check which badges should be unlocked based on stats.

    Args:
        store: Current achievement store (already-unlocked badges).
        stats: Merged stats dict. If None, uses store.stats_snapshot.

    Returns:
        List of newly unlocked Badge objects.
    """
    if stats is None:
        stats = store.stats_snapshot

    now = datetime.now().isoformat()
    newly_unlocked: List[Badge] = []

    # First pass: check all non-meta badges
    for defn in BADGE_CATALOGUE:
        name = defn["name"]
        if name in store.unlocked:
            continue
        if defn["category"] == "meta" and name == "Achievement Hunter":
            continue  # handled in second pass
        if _evaluate_criterion(defn["criteria"], stats):
            store.unlocked[name] = now
            newly_unlocked.append(_make_badge(defn, unlocked_at=now))

    # Second pass: check meta badges (depend on unlock count)
    current_count = len(store.unlocked)
    stats["unlocked_count"] = current_count
    for defn in BADGE_CATALOGUE:
        name = defn["name"]
        if name in store.unlocked:
            continue
        if defn["category"] != "meta":
            continue
        if _evaluate_criterion(defn["criteria"], stats):
            store.unlocked[name] = now
            newly_unlocked.append(_make_badge(defn, unlocked_at=now))

    store.stats_snapshot = dict(stats)
    return newly_unlocked


def all_badges(store: Optional[AchievementStore] = None) -> List[Badge]:
    """Return all badge definitions with their unlock status.

    Args:
        store: If provided, badges in store.unlocked get their timestamp set.

    Returns:
        Full list of Badge objects (unlocked and locked).
    """
    result: List[Badge] = []
    unlocked = store.unlocked if store else {}
    for defn in BADGE_CATALOGUE:
        ts = unlocked.get(defn["name"])
        result.append(_make_badge(defn, unlocked_at=ts))
    return result


# ---------------------------------------------------------------------------
# Aggregate scanner
# ---------------------------------------------------------------------------

def gather_all_stats(
    claude_dir: Optional[str] = None,
) -> Dict[str, Any]:
    """Scan all data sources and return merged stats dict.

    Args:
        claude_dir: Root of the .claude directory. Defaults to ~/.claude.
    """
    if claude_dir is None:
        claude_dir = os.path.expanduser("~/.claude")

    projects_dir = os.path.join(claude_dir, "projects")
    learning_dir = os.path.join(claude_dir, "learning")
    cache_dir = os.path.join(claude_dir, "cache")

    stats: Dict[str, Any] = {}

    # Session stats
    session_stats = scan_sessions(projects_dir)
    stats.update(session_stats)

    # Compute streak from usage dates
    stats["usage_streak"] = compute_usage_streak(
        session_stats.get("usage_dates", [])
    )

    # Converge stats
    converge_stats = scan_converge_history(learning_dir)
    # Merge without overwriting session stats
    for k, v in converge_stats.items():
        if k not in stats or (isinstance(v, (int, float)) and v > stats.get(k, 0)):
            stats[k] = v

    # Genome stats
    genome_stats = scan_genome_history(cache_dir)
    stats.update(genome_stats)

    # Skill stats
    skill_stats = scan_skills(learning_dir)
    stats.update(skill_stats)

    return stats


# ---------------------------------------------------------------------------
# Display formatting
# ---------------------------------------------------------------------------

def format_table(badges: List[Badge]) -> str:
    """Format badges as an ASCII table."""
    lines: List[str] = []
    header = f"{'Badge':<24} {'Category':<12} {'Status':<10} {'Date':<20}"
    sep = "-" * len(header)
    lines.append(sep)
    lines.append(header)
    lines.append(sep)

    for b in badges:
        status = "UNLOCKED" if b.is_unlocked else "locked"
        date_str = b.unlocked_at[:10] if b.unlocked_at else ""
        lines.append(f"{b.icon + ' ' + b.name:<24} {b.category:<12} {status:<10} {date_str:<20}")

    lines.append(sep)

    unlocked_count = sum(1 for b in badges if b.is_unlocked)
    lines.append(f"{unlocked_count}/{len(badges)} badges unlocked")
    return "\n".join(lines)


def format_json(badges: List[Badge], stats: Optional[Dict[str, Any]] = None) -> str:
    """Format badges as JSON."""
    output = {
        "badges": [b.to_dict() for b in badges],
        "unlocked_count": sum(1 for b in badges if b.is_unlocked),
        "total_count": len(badges),
    }
    if stats:
        output["stats"] = {
            k: v for k, v in stats.items() if k != "usage_dates"
        }
    return json.dumps(output, indent=2)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="GRIP Achievement System -- milestone badges"
    )
    parser.add_argument(
        "command",
        nargs="?",
        choices=["check", "unlock"],
        default="check",
        help="check: show badges | unlock: scan and unlock earned badges",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Machine-readable JSON output",
    )
    parser.add_argument(
        "--claude-dir",
        default=None,
        help="Path to .claude directory (default: ~/.claude)",
    )

    args = parser.parse_args(argv)

    claude_dir = args.claude_dir or os.path.expanduser("~/.claude")
    cache_dir = os.path.join(claude_dir, "cache")

    store = load_store(cache_dir)

    if args.command == "unlock":
        stats = gather_all_stats(claude_dir)
        newly_unlocked = check_unlocks(store, stats)
        save_store(store, cache_dir)

        badges = all_badges(store)

        if args.json:
            print(format_json(badges, stats))
        else:
            print(format_table(badges))
            if newly_unlocked:
                print()
                print(f"Newly unlocked: {', '.join(b.name for b in newly_unlocked)}")
            else:
                print("\nNo new badges unlocked.")
        return 0

    else:  # check
        badges = all_badges(store)
        if args.json:
            print(format_json(badges, store.stats_snapshot or None))
        else:
            print(format_table(badges))
        return 0


if __name__ == "__main__":
    sys.exit(main())
