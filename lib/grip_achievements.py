#!/usr/bin/env python3
"""
GRIP Achievement System — Unlockable badges for session milestones.

Tracks progress across sessions and awards achievements when thresholds
are crossed. Achievements persist in state/session/achievements.json.

Usage:
    python3 lib/grip_achievements.py check     # Check + award new achievements
    python3 lib/grip_achievements.py show      # Show unlocked achievements
    python3 lib/grip_achievements.py badge     # Show latest badge for splash screen

VERSION: 1.0.0
DATE: 2026-03-07
"""

import glob
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", os.path.expanduser("~/.claude")))
ACHIEVEMENTS_FILE = CLAUDE_DIR / "state" / "session" / "achievements.json"

# Achievement definitions: (id, name, icon, description, check_fn)
# Icons use Unicode symbols that render well in terminals
ACHIEVEMENTS = [
    {
        "id": "first_blood",
        "name": "First Blood",
        "icon": "⚔",
        "description": "First PR merged",
        "check": lambda s: s.get("prs_merged", 0) >= 1,
    },
    {
        "id": "centurion",
        "name": "Centurion",
        "icon": "C",
        "description": "100 PRs merged",
        "check": lambda s: s.get("prs_merged", 0) >= 100,
    },
    {
        "id": "test_pioneer",
        "name": "Test Pioneer",
        "icon": "T",
        "description": "1,000 tests in suite",
        "check": lambda s: s.get("test_count", 0) >= 1000,
    },
    {
        "id": "test_titan",
        "name": "Test Titan",
        "icon": "T+",
        "description": "10,000 tests in suite",
        "check": lambda s: s.get("test_count", 0) >= 10000,
    },
    {
        "id": "test_colossus",
        "name": "Test Colossus",
        "icon": "T++",
        "description": "20,000 tests in suite",
        "check": lambda s: s.get("test_count", 0) >= 20000,
    },
    {
        "id": "skill_hoarder",
        "name": "Skill Hoarder",
        "icon": "S",
        "description": "100+ skills",
        "check": lambda s: s.get("skill_count", 0) >= 100,
    },
    {
        "id": "generation_x",
        "name": "Generation X",
        "icon": "G",
        "description": "Reached Gen 100",
        "check": lambda s: s.get("generation", 0) >= 100,
    },
    {
        "id": "generation_500",
        "name": "Half Millennium",
        "icon": "G+",
        "description": "Reached Gen 500",
        "check": lambda s: s.get("generation", 0) >= 500,
    },
    {
        "id": "genome_evolved",
        "name": "Evolved",
        "icon": "E",
        "description": "Genome generation 10+",
        "check": lambda s: s.get("genome_gen", 0) >= 10,
    },
    {
        "id": "genome_mature",
        "name": "Mature Genome",
        "icon": "E+",
        "description": "Genome with 100+ genes",
        "check": lambda s: s.get("genome_genes", 0) >= 100,
    },
    {
        "id": "memory_keeper",
        "name": "Memory Keeper",
        "icon": "K",
        "description": "50+ KONO permanent memories",
        "check": lambda s: s.get("kono_permanent", 0) >= 50,
    },
    {
        "id": "mode_master",
        "name": "Mode Master",
        "icon": "M",
        "description": "25+ reasoning modes",
        "check": lambda s: s.get("mode_count", 0) >= 25,
    },
    {
        "id": "marathon_runner",
        "name": "Marathon Runner",
        "icon": "R",
        "description": "Session with 500+ messages",
        "check": lambda s: s.get("max_session_msgs", 0) >= 500,
    },
    {
        "id": "iron_gate",
        "name": "Iron Gate",
        "icon": "I",
        "description": "10,000+ gate evaluations",
        "check": lambda s: s.get("gate_evals", 0) >= 10000,
    },
]


_PR_TYPES = ("feat:", "fix:", "chore:", "docs:", "test:", "refactor:", "perf:")


def _is_pr_line(line: str) -> bool:
    """A merged-PR commit line: has a (#NNN) ref and a conventional type.

    Extracted so _stat_prs has a single loop (the constant-size type scan
    lives here) — keeps complexity linear and out of the nested-loop heuristic.
    """
    low = line.lower()
    return "(#" in line and any(t in low for t in _PR_TYPES)


def _stat_prs() -> dict:
    """Merged-PR count from git history (conventional-commit subjects)."""
    try:
        result = subprocess.run(
            ["git", "-C", str(CLAUDE_DIR), "log", "--oneline", "--all"],
            capture_output=True, text=True, timeout=10,
        )
        count = sum(1 for line in result.stdout.splitlines()
                    if _is_pr_line(line))
        return {"prs_merged": count}
    except Exception:
        return {"prs_merged": 0}


def _stat_counts() -> dict:
    """Skill/mode counts from the cached splash-counts file (absent if none)."""
    try:
        count_file = CLAUDE_DIR / "state" / "session" / "splash-counts.json"
        if count_file.exists():
            data = json.loads(count_file.read_text())
            return {"skill_count": data.get("skills", 0),
                    "mode_count": data.get("modes", 0)}
    except Exception:
        pass
    return {}


def _first_int_token(line: str):
    """First whitespace-delimited all-digit token in ``line``, or None.

    Extracted so _stat_test_count keeps a single loop and preserves the
    exact prior semantics (``str.isdigit`` per token, not a regex — so
    'abc123' is NOT a match, matching the original behaviour).
    """
    for tok in line.split():
        if tok.isdigit():
            return int(tok)
    return None


def _stat_test_count() -> dict:
    """Collected pytest count (last 'collected' line wins, matching prior)."""
    try:
        result = subprocess.run(
            ["python3", "-m", "pytest", str(CLAUDE_DIR / "tests"), "--co", "-q"],
            capture_output=True, text=True, timeout=30, cwd=str(CLAUDE_DIR),
        )
        found: dict = {}
        for line in result.stdout.splitlines():
            if "test" in line and "collected" in line:
                n = _first_int_token(line)
                if n is not None:
                    found = {"test_count": n}
        return found
    except Exception:
        return {"test_count": 0}


def _stat_generation() -> dict:
    """Highest Gen number seen across project memory (absent if none)."""
    try:
        result = subprocess.run(
            ["rg", "-m1", r"^Gen (\d+)", "-o", "-r", "$1",
             str(CLAUDE_DIR / "projects")],
            capture_output=True, text=True, timeout=5,
        )
        if result.stdout.strip():
            return {"generation": int(result.stdout.strip().splitlines()[0])}
        return {}
    except Exception:
        return {"generation": 0}


def _stat_genome() -> dict:
    """Genome generation + gene count (absent if no genome file)."""
    try:
        genome_file = CLAUDE_DIR / "cache" / "genome.json"
        if not genome_file.exists():
            genome_file = CLAUDE_DIR / "data" / "genomes" / "grip.json"
        if genome_file.exists():
            d = json.loads(genome_file.read_text())
            return {"genome_gen": d.get("generation", 0),
                    "genome_genes": len(d.get("genes", []))}
    except Exception:
        pass
    return {}


def _stat_kono() -> dict:
    """Count of permanent KONO memories (absent if no kono dir)."""
    try:
        kono_dir = CLAUDE_DIR / "kono"
        if not kono_dir.exists():
            return {}
        count = 0
        for f in kono_dir.glob("*.json"):
            try:
                if json.loads(f.read_text()).get("permanent"):
                    count += 1
            except Exception:
                pass
        return {"kono_permanent": count}
    except Exception:
        return {"kono_permanent": 0}


def _stat_gates() -> dict:
    """Total gate evaluations from the metrics log (absent if no file)."""
    try:
        metrics_file = CLAUDE_DIR / "state" / "metrics" / "gate-metrics.jsonl"
        if metrics_file.exists():
            with open(metrics_file) as f:
                return {"gate_evals": sum(1 for _ in f)}
    except Exception:
        return {"gate_evals": 0}
    return {}


def _stat_max_msgs() -> dict:
    """Largest session message count seen across project memory."""
    try:
        result = subprocess.run(
            ["rg", "-m1", r"(\d+) messages", "-o", "-r", "$1",
             str(CLAUDE_DIR / "projects")],
            capture_output=True, text=True, timeout=5,
        )
        if result.stdout.strip():
            msgs = [int(x) for x in result.stdout.strip().splitlines()
                    if x.isdigit()]
            return {"max_session_msgs": max(msgs) if msgs else 0}
        return {}
    except Exception:
        return {"max_session_msgs": 0}


def _gather_stats() -> dict:
    """Gather current GRIP stats for achievement checking.

    Coordinator: each helper gathers one independent stat with its own
    error handling and absent-vs-default semantics. The output dict is
    identical to the prior monolithic implementation (keys are disjoint,
    so merge order is irrelevant). Decomposed for KISS/BIG-O — each helper
    now has at most one loop, so no function trips the nested-loop heuristic.
    """
    stats: dict = {}
    for gather in (
        _stat_prs, _stat_counts, _stat_test_count, _stat_generation,
        _stat_genome, _stat_kono, _stat_gates, _stat_max_msgs,
    ):
        stats.update(gather())
    return stats


def load_achievements() -> dict:
    """Load achievement state."""
    try:
        if ACHIEVEMENTS_FILE.exists():
            return json.loads(ACHIEVEMENTS_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        pass
    return {"unlocked": {}, "last_check": None}


def save_achievements(state: dict) -> None:
    """Save achievement state."""
    ACHIEVEMENTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(ACHIEVEMENTS_FILE, "w") as f:
        json.dump(state, f, indent=2)
    os.chmod(ACHIEVEMENTS_FILE, 0o600)


def check_achievements() -> list[str]:
    """Check for newly unlocked achievements. Returns list of new achievement names."""
    stats = _gather_stats()
    state = load_achievements()
    newly_unlocked = []

    for ach in ACHIEVEMENTS:
        aid = ach["id"]
        if aid in state["unlocked"]:
            continue
        try:
            if ach["check"](stats):
                state["unlocked"][aid] = {
                    "name": ach["name"],
                    "icon": ach["icon"],
                    "description": ach["description"],
                    "unlocked_at": datetime.now(timezone.utc).isoformat(),
                    "stats_snapshot": {k: v for k, v in stats.items()
                                       if isinstance(v, (int, float))},
                }
                newly_unlocked.append(ach["name"])
        except Exception:
            continue

    state["last_check"] = datetime.now(timezone.utc).isoformat()
    save_achievements(state)
    return newly_unlocked


def show_achievements() -> str:
    """Format all achievements for display."""
    state = load_achievements()
    lines = ["GRIP Achievements", "=" * 40]

    for ach in ACHIEVEMENTS:
        aid = ach["id"]
        if aid in state["unlocked"]:
            info = state["unlocked"][aid]
            lines.append(f"  [{info['icon']}] {info['name']} — {info['description']}")
        else:
            lines.append(f"  [ ] {ach['name']} — {ach['description']}")

    unlocked = len(state["unlocked"])
    total = len(ACHIEVEMENTS)
    lines.append(f"\n  {unlocked}/{total} unlocked")
    return "\n".join(lines)


def _latest_badge_raw() -> str:
    """Old compact icon line. Kept for power users via `badge --raw` — the
    icons are unreadable to a first-time viewer, which is exactly why the
    default switched to plain language (see latest_badge)."""
    state = load_achievements()
    if not state["unlocked"]:
        return ""
    badges = [
        f"[{state['unlocked'][a['id']]['icon']}]"
        for a in ACHIEVEMENTS
        if a["id"] in state["unlocked"]
    ]
    return f"  Badges: {' '.join(badges)} ({len(badges)}/{len(ACHIEVEMENTS)})"


def latest_badge() -> str:
    """Plain-language milestone line for the splash screen.

    A first-time viewer must understand it without a legend, so instead of
    cryptic icons like "[⚔] [C] [T+]" it states WHAT (how many milestones,
    and the newest one by name), WHY (they mark capability thresholds the
    system has crossed) and HOW (the rest unlock automatically as it grows).
    """
    state = load_achievements()
    unlocked = state.get("unlocked", {})
    if not unlocked:
        return ""
    total = len(ACHIEVEMENTS)
    count = len(unlocked)
    # Newest = the most recently unlocked (ISO-8601 sorts lexicographically).
    newest_id = max(unlocked, key=lambda aid: unlocked[aid].get("unlocked_at", ""))
    info = unlocked[newest_id]
    name = info.get("name", "milestone")
    desc = info.get("description", "")
    return (
        f'  Milestones: {count}/{total} reached — newest "{name}": {desc}. '
        f"(Capability thresholds GRIP has crossed; the rest unlock "
        f"automatically as it grows.)"
    )


def main():
    if len(sys.argv) < 2:
        print("Usage: grip_achievements.py [check|show|badge [--raw]]")
        sys.exit(1)

    cmd = sys.argv[1]
    if cmd == "check":
        new = check_achievements()
        if new:
            print(f"New achievements unlocked: {', '.join(new)}")
        else:
            print("No new achievements")
    elif cmd == "show":
        print(show_achievements())
    elif cmd == "badge":
        raw = "--raw" in sys.argv[2:]
        badge = _latest_badge_raw() if raw else latest_badge()
        if badge:
            print(badge)
    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)


if __name__ == "__main__":
    main()
