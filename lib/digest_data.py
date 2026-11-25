"""
digest_data.py — Shared DigestData model for GRIP multi-platform output.

Extracts structured data from GRIP's memory subsystems. Used by:
- grip_save.py (terminal output with OSC 8 links)
- mcp_app_generator.py (MCP App digest widget)
- slack_canvas_digest.py (Slack Canvas)
- grip_viewer.py (Rich TUI viewer)

Usage:
    from digest_data import collect_digest_data

    data = collect_digest_data()
    # data = {"achievements": [...], "active_state": [...], "genome": {...}, ...}
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import TypedDict

CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", Path.home() / ".claude"))


class GenomePulse(TypedDict, total=False):
    generation: int
    gene_count: int
    fitness: float
    trend: str  # triangle char
    top_genes: list[str]
    runs: int


class DigestData(TypedDict, total=False):
    achievements: list[str]
    active_state: list[str]
    genome: GenomePulse | None
    todo_pulse: str | None
    project_path: str
    encoded_path: str


def _get_encoded_path(project_dir: str | None = None) -> str:
    """Encode a project path for GRIP's project directory structure."""
    cwd = project_dir or os.getcwd()
    return cwd.replace("/", "-").replace(".", "-")


def parse_achievements() -> list[str]:
    """Extract recent achievements from session-context.md."""
    ctx_path = CLAUDE_DIR / "rules" / "session-context.md"
    achievements: list[str] = []
    if not ctx_path.exists():
        return achievements
    try:
        in_section = False
        for line in ctx_path.read_text(encoding="utf-8", errors="replace").splitlines():
            if "## Recent Achievements" in line:
                in_section = True
                continue
            elif line.startswith("## "):
                in_section = False
                continue
            if in_section and line.startswith("- ") and len(achievements) < 3:
                entry = line[2:].strip()
                entry = re.sub(r'\s*\(last:.*$', '', entry)
                entry = re.sub(r'\s*\(.*?\)\s*$', '', entry)
                if len(entry) > 55:
                    entry = entry[:entry.rfind(' ', 0, 55)] + "..."
                achievements.append(entry)
    except Exception:
        pass
    return achievements


def parse_active_state(encoded: str | None = None) -> list[str]:
    """Extract active focus and TODO pulse from MEMORY.md.

    Returns list of strings: active work items + TODO pulse summary.
    """
    if encoded is None:
        encoded = _get_encoded_path()
    mem_path = CLAUDE_DIR / "projects" / encoded / "memory" / "MEMORY.md"
    items: list[str] = []
    if not mem_path.exists():
        return items
    try:
        text = mem_path.read_text()
        in_current = False
        for line in text.splitlines():
            if line.startswith("## Current Work"):
                in_current = True
                continue
            elif line.startswith("## ") and in_current:
                break
            if in_current and line.startswith("### "):
                title = line.lstrip("# ").strip()
                if len(title) > 55:
                    title = title[:title.rfind(' ', 0, 55)] + "..."
                items.append(title)
                if len(items) >= 2:
                    break

        todo_match = re.search(
            r'(\d+)\s+items\s+total.*?(\d+)\s+PENDING.*?(\d+)\s+IN PROGRESS',
            text, re.IGNORECASE)
        if todo_match:
            total, pending, active = todo_match.groups()
            items.append(
                f"{total} items tracked \u00b7 {pending} pending"
                f" \u00b7 {active} in progress")
    except Exception:
        pass
    return items


def read_genome_summary() -> GenomePulse | None:
    """Extract genome stats and top 3 genes by fitness."""
    genome_path = CLAUDE_DIR / "cache" / "genome.json"
    if not genome_path.exists():
        return None
    try:
        g = json.loads(genome_path.read_text())
        genes = g.get("genes", {})
        top = sorted(
            genes.items(),
            key=lambda x: x[1].get("fitness", 0),
            reverse=True,
        )[:3]
        history = g.get("fitness_history", [0])
        fitness = history[-1]
        prev = history[-2] if len(history) >= 2 else fitness
        if fitness > prev + 0.002:
            trend = "\u25b2"  # up triangle
        elif fitness < prev - 0.002:
            trend = "\u25bc"  # down triangle
        else:
            trend = "\u25ac"  # horizontal bar
        return GenomePulse(
            generation=g.get("generation", 0),
            gene_count=len(genes),
            fitness=fitness,
            trend=trend,
            top_genes=[name for name, _ in top],
            runs=g.get("total_converge_runs", 0),
        )
    except Exception:
        return None


def collect_digest_data(project_dir: str | None = None) -> DigestData:
    """Collect all digest data from GRIP memory subsystems.

    Args:
        project_dir: Override project directory. Defaults to CWD.

    Returns:
        DigestData dict with all fields populated (or empty lists/None).
    """
    encoded = _get_encoded_path(project_dir)
    active = parse_active_state(encoded)

    # Extract todo_pulse from active_state (last item if it matches pattern)
    todo_pulse = None
    if active and "items tracked" in active[-1]:
        todo_pulse = active[-1]

    return DigestData(
        achievements=parse_achievements(),
        active_state=active,
        genome=read_genome_summary(),
        todo_pulse=todo_pulse,
        project_path=project_dir or os.getcwd(),
        encoded_path=encoded,
    )
