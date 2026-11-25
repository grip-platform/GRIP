"""
grip_viewer.py -- Rich TUI digest viewer for GRIP memory state.

Renders DigestData as a styled terminal display using Rich (if available)
or falls back to plain text with Unicode box-drawing characters.

Usage:
    python3 lib/grip_viewer.py           # Rich TUI (or plain fallback)
    python3 lib/grip_viewer.py --json    # Raw DigestData as JSON
    python3 lib/grip_viewer.py --plain   # Force plain text mode
"""

from __future__ import annotations

import json
import sys
from typing import Any

# ---------------------------------------------------------------------------
# Rich availability detection
# ---------------------------------------------------------------------------

_RICH_AVAILABLE = False

try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text

    _RICH_AVAILABLE = True
except ImportError:
    pass


def is_rich_available() -> bool:
    """Return True if the rich library is importable."""
    return _RICH_AVAILABLE


# ---------------------------------------------------------------------------
# Plain text renderer (Unicode box-drawing fallback)
# ---------------------------------------------------------------------------

_BOX_H = "\u2500"   # horizontal line
_BOX_V = "\u2502"   # vertical line
_BOX_TL = "\u256d"  # top-left corner
_BOX_TR = "\u256e"  # top-right corner
_BOX_BL = "\u2570"  # bottom-left corner
_BOX_BR = "\u256f"  # bottom-right corner
_BULLET_G = "\u25cf"  # green bullet (filled circle)
_BULLET_Y = "\u25cb"  # yellow bullet (open circle)
_BAR_FULL = "\u2588"  # full block
_BAR_EMPTY = "\u2591"  # light shade


def _plain_box(title: str, lines: list[str], width: int = 60) -> str:
    """Render a box with Unicode box-drawing characters."""
    inner = width - 2
    parts: list[str] = []
    # Title bar
    pad = inner - len(title) - 2
    parts.append(f"{_BOX_TL}{_BOX_H} {title} {_BOX_H * max(pad, 0)}{_BOX_TR}")
    for line in lines:
        text = line[:inner]
        parts.append(f"{_BOX_V} {text:<{inner - 1}}{_BOX_V}")
    parts.append(f"{_BOX_BL}{_BOX_H * inner}{_BOX_BR}")
    return "\n".join(parts)


def _plain_progress(value: float, width: int = 20) -> str:
    """Render a plain text progress bar."""
    filled = int(value * width)
    return f"[{_BAR_FULL * filled}{_BAR_EMPTY * (width - filled)}] {value:.1%}"


def render_plain(data: dict[str, Any]) -> str:
    """Render DigestData as plain text with Unicode box-drawing."""
    content_sections: list[str] = []

    # Achievements
    achievements = data.get("achievements", [])
    if achievements:
        lines = [f"  {_BULLET_G} {a}" for a in achievements]
        content_sections.append(_plain_box("Achievements", lines))

    # Active State
    active = data.get("active_state", [])
    if active:
        lines = [f"  {_BULLET_Y} {item}" for item in active
                 if "items tracked" not in item]
        if lines:
            content_sections.append(_plain_box("Active Work", lines))

    # Genome
    genome = data.get("genome")
    if genome:
        fitness = genome.get("fitness", 0)
        lines = [
            f"  Generation: {genome.get('generation', '?')} "
            f"{genome.get('trend', '')}",
            f"  Genes: {genome.get('gene_count', '?')} "
            f"| Runs: {genome.get('runs', '?')}",
            f"  Fitness: {_plain_progress(fitness)}",
        ]
        top = genome.get("top_genes", [])
        if top:
            lines.append(f"  Top: {', '.join(top)}")
        content_sections.append(_plain_box("Genome", lines))

    # TODO pulse
    todo = data.get("todo_pulse")
    if todo:
        content_sections.append(_plain_box("TODO Pulse", [f"  {todo}"]))

    # Build final output
    header_lines = [f"Project: {data.get('project_path', 'unknown')}"]
    if not content_sections:
        header_lines.append("No data available")

    sections = [_plain_box("GRIP Memory Digest", header_lines)]
    sections.extend(content_sections)

    return "\n\n".join(sections)


# ---------------------------------------------------------------------------
# Rich renderer
# ---------------------------------------------------------------------------

def render_rich(data: dict[str, Any]) -> str:
    """Render DigestData as Rich-formatted string."""
    from io import StringIO

    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text

    buf = StringIO()
    console = Console(file=buf, width=72, force_terminal=True)

    renderables: list[Any] = []

    # Achievements table
    achievements = data.get("achievements", [])
    if achievements:
        tbl = Table(
            show_header=True, header_style="bold green",
            box=None, pad_edge=False,
        )
        tbl.add_column("Achievements", style="green")
        for a in achievements:
            tbl.add_row(f"  {_BULLET_G} {a}")
        renderables.append(tbl)

    # Active work table
    active = data.get("active_state", [])
    active_items = [item for item in active if "items tracked" not in item]
    if active_items:
        tbl = Table(
            show_header=True, header_style="bold yellow",
            box=None, pad_edge=False,
        )
        tbl.add_column("Active Work", style="yellow")
        for item in active_items:
            tbl.add_row(f"  {_BULLET_Y} {item}")
        renderables.append(tbl)

    # Genome section
    genome = data.get("genome")
    if genome:
        fitness = genome.get("fitness", 0)
        filled = int(fitness * 20)
        bar = f"[green]{'=' * filled}[/green][dim]{'.' * (20 - filled)}[/dim]"
        genome_text = Text.from_markup(
            f"  Gen {genome.get('generation', '?')} "
            f"{genome.get('trend', '')}  "
            f"Genes: {genome.get('gene_count', '?')}  "
            f"Runs: {genome.get('runs', '?')}\n"
            f"  Fitness: [{bar}] {fitness:.1%}"
        )
        top = genome.get("top_genes", [])
        if top:
            genome_text.append(f"\n  Top: {', '.join(top)}")
        genome_panel = Panel(
            genome_text, title="Genome", border_style="cyan",
            padding=(0, 1),
        )
        renderables.append(genome_panel)

    # TODO pulse
    todo = data.get("todo_pulse")
    if todo:
        renderables.append(Text(f"\n  {todo}", style="dim"))

    # No data case
    if not renderables:
        renderables.append(Text("  No data available", style="dim"))

    # Wrap everything in outer panel
    subtitle = data.get("project_path", "")
    if len(subtitle) > 50:
        subtitle = "..." + subtitle[-47:]

    from rich.console import Group
    inner = Group(*renderables)
    outer = Panel(
        inner,
        title="GRIP Memory Digest",
        subtitle=subtitle,
        border_style="bright_blue",
        padding=(1, 2),
    )
    console.print(outer)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def render_digest(data: dict[str, Any] | None = None, plain: bool = False) -> str:
    """Render DigestData as a formatted string.

    Args:
        data: DigestData dict. If None, collects live data.
        plain: Force plain text mode even if Rich is available.

    Returns:
        Formatted string ready for terminal output.
    """
    if data is None:
        from digest_data import collect_digest_data
        data = collect_digest_data()

    if plain or not is_rich_available():
        return render_plain(data)
    return render_rich(data)


def view_digest(plain: bool = False) -> None:
    """Collect digest data and print to console."""
    output = render_digest(plain=plain)
    print(output)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    """CLI entry point."""
    args = sys.argv[1:]

    if "--json" in args:
        from digest_data import collect_digest_data
        data = collect_digest_data()
        print(json.dumps(data, indent=2, default=str))
        return

    plain = "--plain" in args
    view_digest(plain=plain)


if __name__ == "__main__":
    main()
