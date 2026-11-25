#!/usr/bin/env python3
"""GRIP Status Reporter -- single-command system health dashboard.

Aggregates 8 subsystems into a bordered ASCII report with
colour-coded health indicators. JSON output mode for automation.

Usage:
    python3 lib/status_reporter.py              # full report
    python3 lib/status_reporter.py --json       # machine-readable JSON
    python3 lib/status_reporter.py --section genome  # single section
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any
from path_encoding import encode_project_path


# ---------------------------------------------------------------------------
# Colour helpers
# ---------------------------------------------------------------------------

class Colour:
    """ANSI colour codes with auto-detection of terminal capability."""

    RESET = "\033[0m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    RED = "\033[31m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    CYAN = "\033[36m"

    _enabled: bool | None = None

    @classmethod
    def enabled(cls) -> bool:
        if cls._enabled is not None:
            return cls._enabled
        if os.environ.get("NO_COLOR"):
            cls._enabled = False
        elif os.environ.get("FORCE_COLOR"):
            cls._enabled = True
        else:
            cls._enabled = hasattr(sys.stdout, "isatty") and sys.stdout.isatty()
        return cls._enabled

    @classmethod
    def set_enabled(cls, value: bool) -> None:
        cls._enabled = value

    @classmethod
    def wrap(cls, text: str, code: str) -> str:
        if cls.enabled():
            return f"{code}{text}{cls.RESET}"
        return text


def health_indicator(value: float, green_min: float = 80, yellow_min: float = 50) -> str:
    """Return a colour-coded health dot based on thresholds."""
    if value >= green_min:
        return Colour.wrap("[OK]", Colour.GREEN)
    elif value >= yellow_min:
        return Colour.wrap("[WARN]", Colour.YELLOW)
    else:
        return Colour.wrap("[CRIT]", Colour.RED)


def status_indicator(ok: bool) -> str:
    """Simple ok/not-ok indicator."""
    if ok:
        return Colour.wrap("[OK]", Colour.GREEN)
    return Colour.wrap("[WARN]", Colour.YELLOW)


# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------

def resolve_grip_dir() -> Path:
    """Resolve the GRIP directory (repo root).

    Looks for CLAUDE.md as anchor. Falls back to $HOME/.claude.
    """
    env_dir = os.environ.get("GRIP_DIR")
    if env_dir:
        return Path(env_dir)

    # Walk up from this file to find repo root
    candidate = Path(__file__).resolve().parent.parent
    if (candidate / "CLAUDE.md").exists():
        return candidate

    home_claude = Path.home() / ".claude"
    if home_claude.exists():
        return home_claude

    return candidate


# ---------------------------------------------------------------------------
# Section: Session Info
# ---------------------------------------------------------------------------

def gather_session_info(grip_dir: Path) -> dict[str, Any]:
    """Current branch, gen number from MEMORY.md, uptime estimate."""
    result: dict[str, Any] = {
        "branch": "unknown",
        "generation": None,
        "uptime_estimate": "unknown",
    }

    # Current branch
    try:
        branch = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, timeout=5, cwd=str(grip_dir),
        )
        if branch.returncode == 0:
            result["branch"] = branch.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    # Gen number from MEMORY.md
    memory_path = grip_dir / "projects" / encode_project_path(grip_dir) / "memory" / "MEMORY.md"
    if not memory_path.exists():
        memory_path = grip_dir / "MEMORY.md"
    if memory_path.exists():
        try:
            text = memory_path.read_text(encoding="utf-8")
            match = re.search(r"Gen\s+(\d+)", text)
            if match:
                result["generation"] = int(match.group(1))
        except (OSError, UnicodeDecodeError):
            pass

    # Uptime estimate: time since last session-start hook ran (newest file in cache/)
    cache_dir = grip_dir / "cache"
    if cache_dir.is_dir():
        try:
            newest = max(
                (f.stat().st_mtime for f in cache_dir.iterdir() if f.is_file()),
                default=0,
            )
            if newest > 0:
                import time
                delta = time.time() - newest
                hours = int(delta // 3600)
                minutes = int((delta % 3600) // 60)
                result["uptime_estimate"] = f"{hours}h {minutes}m since last cache write"
        except OSError:
            pass

    return result


def render_session_info(data: dict[str, Any]) -> list[str]:
    lines = []
    lines.append(f"  Branch:     {Colour.wrap(data['branch'], Colour.CYAN)}")
    gen = data.get("generation")
    lines.append(f"  Generation: {gen if gen is not None else 'unknown'}")
    lines.append(f"  Uptime:     {data.get('uptime_estimate', 'unknown')}")
    return lines


# ---------------------------------------------------------------------------
# Section: Genome Health
# ---------------------------------------------------------------------------

def gather_genome_health(grip_dir: Path) -> dict[str, Any]:
    """Load genome.json, report generation, gene count, top-5, dormant."""
    result: dict[str, Any] = {
        "available": False,
        "generation": None,
        "gene_count": 0,
        "top_5": [],
        "dormant_genes": [],
        "health_score": 0,
    }

    genome_path = grip_dir / "data" / "genome.json"
    if not genome_path.exists():
        # Try alternate locations
        for alt in [grip_dir / "genome.json", grip_dir / "cache" / "genome.json"]:
            if alt.exists():
                genome_path = alt
                break
        else:
            return result

    try:
        with open(genome_path, encoding="utf-8") as f:
            genome = json.load(f)
    except (json.JSONDecodeError, OSError):
        return result

    result["available"] = True
    result["generation"] = genome.get("generation", genome.get("gen"))
    genes = genome.get("genes", [])
    result["gene_count"] = len(genes)

    # Sort by fitness descending, take top 5
    sorted_genes = sorted(genes, key=lambda g: g.get("fitness", 0), reverse=True)
    result["top_5"] = [
        {"name": g.get("name", g.get("id", "?")), "fitness": g.get("fitness", 0)}
        for g in sorted_genes[:5]
    ]

    # Dormant genes (fitness <= 0 or explicitly marked)
    result["dormant_genes"] = [
        g.get("name", g.get("id", "?"))
        for g in genes
        if g.get("dormant") or g.get("fitness", 1) <= 0
    ]

    # Health: proportion of genes with fitness > 50
    if genes:
        healthy = sum(1 for g in genes if g.get("fitness", 0) > 50)
        result["health_score"] = round(100 * healthy / len(genes), 1)
    else:
        result["health_score"] = 0

    return result


def render_genome_health(data: dict[str, Any]) -> list[str]:
    lines = []
    if not data["available"]:
        lines.append(f"  {Colour.wrap('No genome.json found', Colour.DIM)}")
        return lines

    lines.append(f"  Generation:  {data['generation']}")
    lines.append(f"  Gene count:  {data['gene_count']}")
    lines.append(f"  Health:      {data['health_score']}% {health_indicator(data['health_score'])}")

    if data["top_5"]:
        lines.append("  Top 5 genes:")
        for g in data["top_5"]:
            lines.append(f"    - {g['name']}: {g['fitness']}")

    if data["dormant_genes"]:
        lines.append(f"  Dormant:     {', '.join(data['dormant_genes'])}")

    return lines


# ---------------------------------------------------------------------------
# Section: Metrics Summary
# ---------------------------------------------------------------------------

def gather_metrics_summary(grip_dir: Path) -> dict[str, Any]:
    """Last 5 converge runs from metrics JSONL, avg score, avg depth."""
    result: dict[str, Any] = {
        "available": False,
        "total_runs": 0,
        "last_5": [],
        "avg_score": 0,
        "avg_depth": 0,
    }

    metrics_path = grip_dir / "learning" / "converge-metrics.jsonl"
    if not metrics_path.exists():
        return result

    try:
        text = metrics_path.read_text(encoding="utf-8")
    except OSError:
        return result

    # Parse all converge_end events
    runs: list[dict[str, Any]] = []
    for line in text.strip().split("\n"):
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if entry.get("event") == "converge_end":
            runs.append({
                "run_id": entry.get("run_id", "?"),
                "score": entry.get("final_score", 0),
                "depth": entry.get("depth_reached", 0),
                "elapsed_min": entry.get("elapsed_minutes", 0),
                "satisfied": entry.get("satisfied", False),
                "commits": entry.get("commits", 0),
            })

    if not runs:
        return result

    result["available"] = True
    result["total_runs"] = len(runs)
    last_5 = runs[-5:]
    result["last_5"] = last_5

    scores = [r["score"] for r in runs]
    depths = [r["depth"] for r in runs]
    result["avg_score"] = round(sum(scores) / len(scores), 1) if scores else 0
    result["avg_depth"] = round(sum(depths) / len(depths), 1) if depths else 0

    return result


def render_metrics_summary(data: dict[str, Any]) -> list[str]:
    lines = []
    if not data["available"]:
        lines.append(f"  {Colour.wrap('No converge metrics found', Colour.DIM)}")
        return lines

    lines.append(f"  Total runs:  {data['total_runs']}")
    lines.append(f"  Avg score:   {data['avg_score']} {health_indicator(data['avg_score'])}")
    lines.append(f"  Avg depth:   {data['avg_depth']}")

    if data["last_5"]:
        lines.append("  Last 5 runs:")
        for r in data["last_5"]:
            sat = Colour.wrap("pass", Colour.GREEN) if r["satisfied"] else Colour.wrap("fail", Colour.RED)
            lines.append(
                f"    - {r['run_id'][:8]}: score={r['score']} "
                f"depth={r['depth']} {r['elapsed_min']}min {sat}"
            )

    return lines


# ---------------------------------------------------------------------------
# Section: Disk Usage
# ---------------------------------------------------------------------------

def _dir_size_bytes(path: Path) -> int:
    """Recursively sum file sizes in a directory."""
    total = 0
    if not path.is_dir():
        return 0
    try:
        for item in path.rglob("*"):
            if item.is_file():
                try:
                    total += item.stat().st_size
                except OSError:
                    pass
    except OSError:
        pass
    return total


def _format_size(nbytes: int) -> str:
    """Human-readable file size."""
    for unit in ("B", "KB", "MB", "GB"):
        if nbytes < 1024:
            return f"{nbytes:.1f} {unit}" if unit != "B" else f"{nbytes} B"
        nbytes /= 1024
    return f"{nbytes:.1f} TB"


def gather_disk_usage(grip_dir: Path) -> dict[str, Any]:
    """Size of cache/, data/, logs/ dirs."""
    dirs = ["cache", "data", "logs"]
    result: dict[str, Any] = {"dirs": {}, "total_bytes": 0}
    total = 0
    for d in dirs:
        p = grip_dir / d
        size = _dir_size_bytes(p)
        result["dirs"][d] = {
            "exists": p.is_dir(),
            "bytes": size,
            "human": _format_size(size),
        }
        total += size
    result["total_bytes"] = total
    result["total_human"] = _format_size(total)
    return result


def render_disk_usage(data: dict[str, Any]) -> list[str]:
    lines = []
    for name, info in data["dirs"].items():
        if info["exists"]:
            lines.append(f"  {name + '/':12s} {info['human']}")
        else:
            lines.append(f"  {name + '/':12s} {Colour.wrap('not found', Colour.DIM)}")
    lines.append(f"  {'Total:':12s} {data['total_human']}")
    return lines


# ---------------------------------------------------------------------------
# Section: Git Status
# ---------------------------------------------------------------------------

def gather_git_status(grip_dir: Path) -> dict[str, Any]:
    """Uncommitted changes count, ahead/behind remote."""
    result: dict[str, Any] = {
        "uncommitted": 0,
        "ahead": 0,
        "behind": 0,
        "clean": True,
    }

    try:
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True, text=True, timeout=5, cwd=str(grip_dir),
        )
        if status.returncode == 0:
            changed = [l for l in status.stdout.strip().split("\n") if l.strip()]
            result["uncommitted"] = len(changed)
            result["clean"] = len(changed) == 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    try:
        revlist = subprocess.run(
            ["git", "rev-list", "--left-right", "--count", "HEAD...@{upstream}"],
            capture_output=True, text=True, timeout=5, cwd=str(grip_dir),
        )
        if revlist.returncode == 0:
            parts = revlist.stdout.strip().split()
            if len(parts) == 2:
                result["ahead"] = int(parts[0])
                result["behind"] = int(parts[1])
    except (subprocess.TimeoutExpired, FileNotFoundError, ValueError):
        pass

    return result


def render_git_status(data: dict[str, Any]) -> list[str]:
    lines = []
    count = data["uncommitted"]
    indicator = status_indicator(count == 0)
    lines.append(f"  Uncommitted: {count} {indicator}")
    lines.append(f"  Ahead:       {data['ahead']}")
    lines.append(f"  Behind:      {data['behind']}")
    return lines


# ---------------------------------------------------------------------------
# Section: Active Hooks
# ---------------------------------------------------------------------------

def gather_active_hooks(grip_dir: Path) -> dict[str, Any]:
    """List executable hooks in hooks/ directory."""
    hooks_dir = grip_dir / "hooks"
    result: dict[str, Any] = {"hooks": [], "total": 0, "executable": 0}

    if not hooks_dir.is_dir():
        return result

    hook_files = []
    try:
        for f in sorted(hooks_dir.iterdir()):
            if f.is_file() and f.suffix in (".py", ".sh"):
                is_exec = os.access(f, os.X_OK)
                hook_files.append({
                    "name": f.name,
                    "executable": is_exec,
                })
    except OSError:
        pass

    result["hooks"] = hook_files
    result["total"] = len(hook_files)
    result["executable"] = sum(1 for h in hook_files if h["executable"])
    return result


def render_active_hooks(data: dict[str, Any]) -> list[str]:
    lines = []
    lines.append(f"  Total:      {data['total']} ({data['executable']} executable)")
    for h in data["hooks"]:
        marker = Colour.wrap("+", Colour.GREEN) if h["executable"] else Colour.wrap("-", Colour.DIM)
        lines.append(f"    {marker} {h['name']}")
    return lines


# ---------------------------------------------------------------------------
# Section: Skills Count
# ---------------------------------------------------------------------------

def gather_skills_count(grip_dir: Path) -> dict[str, Any]:
    """Count SKILL.md files (active vs archived)."""
    skills_dir = grip_dir / "skills"
    result: dict[str, Any] = {"active": 0, "archived": 0, "total": 0}

    if not skills_dir.is_dir():
        return result

    try:
        for skill_file in skills_dir.rglob("SKILL.md"):
            parts = skill_file.relative_to(skills_dir).parts
            if "_archived" in parts:
                result["archived"] += 1
            else:
                result["active"] += 1
    except OSError:
        pass

    result["total"] = result["active"] + result["archived"]
    return result


def render_skills_count(data: dict[str, Any]) -> list[str]:
    lines = []
    lines.append(f"  Active:   {data['active']}")
    lines.append(f"  Archived: {data['archived']}")
    lines.append(f"  Total:    {data['total']}")
    return lines


# ---------------------------------------------------------------------------
# Section: Recent PRs
# ---------------------------------------------------------------------------

def gather_recent_prs(grip_dir: Path) -> dict[str, Any]:
    """Last 5 merged PRs from git log (Merge commits or PR references)."""
    result: dict[str, Any] = {"prs": []}

    try:
        log = subprocess.run(
            ["git", "log", "--oneline", "--grep=PR\\|#\\|Merge pull request", "-n", "50"],
            capture_output=True, text=True, timeout=10, cwd=str(grip_dir),
        )
        if log.returncode != 0:
            # Fallback: just get recent merge commits
            log = subprocess.run(
                ["git", "log", "--oneline", "--merges", "-n", "10"],
                capture_output=True, text=True, timeout=10, cwd=str(grip_dir),
            )
        if log.returncode == 0:
            for line in log.stdout.strip().split("\n")[:5]:
                if line.strip():
                    parts = line.split(" ", 1)
                    sha = parts[0] if parts else "?"
                    msg = parts[1] if len(parts) > 1 else ""
                    # Extract PR number if present
                    pr_match = re.search(r"#(\d+)", msg)
                    result["prs"].append({
                        "sha": sha,
                        "message": msg,
                        "pr_number": int(pr_match.group(1)) if pr_match else None,
                    })
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    return result


def render_recent_prs(data: dict[str, Any]) -> list[str]:
    lines = []
    if not data["prs"]:
        lines.append(f"  {Colour.wrap('No recent PRs found', Colour.DIM)}")
        return lines
    for pr in data["prs"]:
        num = f"#{pr['pr_number']}" if pr["pr_number"] else pr["sha"][:7]
        lines.append(f"  {Colour.wrap(num, Colour.CYAN):20s} {pr['message'][:60]}")
    return lines


# ---------------------------------------------------------------------------
# Report assembly
# ---------------------------------------------------------------------------

SECTIONS = {
    "session": ("Session Info", gather_session_info, render_session_info),
    "genome": ("Genome Health", gather_genome_health, render_genome_health),
    "metrics": ("Metrics Summary", gather_metrics_summary, render_metrics_summary),
    "disk": ("Disk Usage", gather_disk_usage, render_disk_usage),
    "git": ("Git Status", gather_git_status, render_git_status),
    "hooks": ("Active Hooks", gather_active_hooks, render_active_hooks),
    "skills": ("Skills Count", gather_skills_count, render_skills_count),
    "prs": ("Recent PRs", gather_recent_prs, render_recent_prs),
}

SECTION_ORDER = ["session", "genome", "metrics", "disk", "git", "hooks", "skills", "prs"]


def gather_all(grip_dir: Path, section_filter: str | None = None) -> dict[str, Any]:
    """Gather data for all (or filtered) sections."""
    data: dict[str, Any] = {}
    keys = [section_filter] if section_filter and section_filter in SECTIONS else SECTION_ORDER
    for key in keys:
        _, gatherer, _ = SECTIONS[key]
        data[key] = gatherer(grip_dir)
    return data


def render_report(data: dict[str, Any], width: int = 72) -> str:
    """Render bordered ASCII report."""
    border_h = "=" * width
    lines: list[str] = []
    title = " GRIP System Status Report "
    pad = (width - len(title)) // 2
    lines.append(border_h)
    lines.append(f"{'=' * pad}{Colour.wrap(title, Colour.BOLD)}{'=' * (width - pad - len(title))}")
    lines.append(border_h)

    section_keys = [k for k in SECTION_ORDER if k in data]
    for i, key in enumerate(section_keys):
        label, _, renderer = SECTIONS[key]
        lines.append("")
        lines.append(f"  {Colour.wrap(f'[ {label} ]', Colour.BOLD)}")
        lines.append(f"  {'-' * (len(label) + 4)}")
        section_lines = renderer(data[key])
        lines.extend(section_lines)
        if i < len(section_keys) - 1:
            lines.append("")

    lines.append("")
    lines.append(border_h)
    return "\n".join(lines)


def to_json(data: dict[str, Any]) -> str:
    """Serialize report data to JSON."""
    return json.dumps(data, indent=2, default=str)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="GRIP Status Reporter -- system health dashboard",
    )
    parser.add_argument(
        "--json", action="store_true",
        help="Output machine-readable JSON",
    )
    parser.add_argument(
        "--section", type=str, default=None,
        choices=list(SECTIONS.keys()),
        help="Show only a specific section",
    )
    parser.add_argument(
        "--grip-dir", type=str, default=None,
        help="Override GRIP directory path",
    )
    parser.add_argument(
        "--no-color", action="store_true",
        help="Disable colour output",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.no_color:
        Colour.set_enabled(False)

    grip_dir = Path(args.grip_dir) if args.grip_dir else resolve_grip_dir()
    data = gather_all(grip_dir, section_filter=args.section)

    if args.json:
        print(to_json(data))
    else:
        print(render_report(data))

    return 0


if __name__ == "__main__":
    sys.exit(main())
