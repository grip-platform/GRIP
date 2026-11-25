#!/usr/bin/env python3
"""GRIP Dashboard Composer -- unified multi-widget terminal dashboard.

Composes GRIP modules (weather, health, energy, achievements, timeline, genome)
into a single terminal view with bordered boxes and grid layout.

Usage:
    python3 -m lib.grip_dashboard full
    python3 -m lib.grip_dashboard compact
    python3 -m lib.grip_dashboard custom weather+health+genome
    python3 -m lib.grip_dashboard list
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Box-drawing characters
BOX_TL = "\u250c"
BOX_TR = "\u2510"
BOX_BL = "\u2514"
BOX_BR = "\u2518"
BOX_H = "\u2500"
BOX_V = "\u2502"

MIN_BOX_WIDTH = 10


def render_box(title: str, content_lines: List[str], width: int) -> List[str]:
    """Draw a bordered box around content lines.

    Args:
        title: Box title shown in the top border.
        content_lines: Lines of text to display inside.
        width: Total box width including borders.

    Returns:
        List of strings representing the box.
    """
    if width < MIN_BOX_WIDTH:
        width = MIN_BOX_WIDTH

    inner = width - 2  # subtract left and right border chars

    # Top border: ┌─ Title ──────┐
    title_display = title[:inner - 4] if len(title) > inner - 4 else title
    if title_display:
        top_fill = inner - len(title_display) - 3  # "─ Title " + fill
        top = f"{BOX_TL}{BOX_H} {title_display} {BOX_H * max(0, top_fill)}{BOX_TR}"
    else:
        top = f"{BOX_TL}{BOX_H * inner}{BOX_TR}"

    # Pad top to exact width if needed
    if len(top) < width:
        # Insert extra horizontal lines before TR
        deficit = width - len(top)
        top = top[:-1] + BOX_H * deficit + BOX_TR

    # Content lines
    rows = []
    for line in content_lines:
        text = line[:inner - 2]  # leave 1 space padding each side
        padded = f" {text}{' ' * max(0, inner - len(text) - 2)} "
        # Ensure padded is exactly inner chars
        if len(padded) < inner:
            padded = padded + " " * (inner - len(padded))
        elif len(padded) > inner:
            padded = padded[:inner]
        rows.append(f"{BOX_V}{padded}{BOX_V}")

    # If no content, show one empty line
    if not rows:
        rows.append(f"{BOX_V}{' ' * inner}{BOX_V}")

    # Bottom border
    bottom = f"{BOX_BL}{BOX_H * inner}{BOX_BR}"

    return [top] + rows + [bottom]


class Widget(ABC):
    """Base class for dashboard widgets."""

    name: str = "Widget"
    description: str = ""

    @abstractmethod
    def render(self, width: int, height: int) -> List[str]:
        """Render widget content as lines of text (without box border).

        Args:
            width: Available content width (inside borders).
            height: Maximum number of content lines.

        Returns:
            List of content strings.
        """
        ...

    def render_boxed(self, width: int, height: int) -> List[str]:
        """Render widget inside a bordered box."""
        content = self.render(width - 4, height)  # inner padding
        return render_box(self.name, content, width)


class WeatherWidget(Widget):
    """Codebase weather from converge_weather module."""

    name = "Weather"
    description = "Convergence weather report"

    def render(self, width: int, height: int) -> List[str]:
        try:
            from lib.converge_weather import WeatherReport

            report = WeatherReport.generate()
            lines = [
                f"Condition: {report.condition}",
                f"Velocity: {report.velocity:.2f}",
                f"Anomalies: {len(report.anomalies)} active",
            ]
            if report.forecast:
                lines.append(f"Forecast: {report.forecast}")
            return lines[:height]
        except Exception:
            return self._placeholder(height)

    @staticmethod
    def _placeholder(height: int) -> List[str]:
        lines = ["Weather: N/A", "Module unavailable"]
        return lines[:height]


class HealthWidget(Widget):
    """System health from grip_health_cli module."""

    name = "Health"
    description = "GRIP subsystem health"

    def render(self, width: int, height: int) -> List[str]:
        try:
            from lib.grip_health_cli import generate_report

            report = generate_report()
            lines = [f"Overall: {report.overall_status}"]
            for sub in report.subsystems[:height - 1]:
                lines.append(f"  {sub.name}: {sub.status}")
            return lines[:height]
        except Exception:
            return self._placeholder(height)

    @staticmethod
    def _placeholder(height: int) -> List[str]:
        lines = ["Health: N/A", "Module unavailable"]
        return lines[:height]


class EnergyWidget(Widget):
    """Session energy from session_energy module."""

    name = "Energy"
    description = "Session energy profile"

    def render(self, width: int, height: int) -> List[str]:
        try:
            from lib.session_energy import energy_summary

            summary = energy_summary()
            lines = summary.split("\n") if isinstance(summary, str) else [str(summary)]
            return lines[:height]
        except Exception:
            return self._placeholder(height)

    @staticmethod
    def _placeholder(height: int) -> List[str]:
        lines = ["Energy: N/A", "Module unavailable"]
        return lines[:height]


class AchievementWidget(Widget):
    """Achievements from achievements module."""

    name = "Achievements"
    description = "Badge and milestone tracker"

    def render(self, width: int, height: int) -> List[str]:
        try:
            from lib.achievements import load_store

            store = load_store()
            badges = store.get("badges", [])
            total = len(badges)
            lines = [f"Badges earned: {total}"]
            for badge in badges[-min(height - 1, 4):]:
                name = badge.get("name", badge) if isinstance(badge, dict) else str(badge)
                lines.append(f"  {name}")
            return lines[:height]
        except Exception:
            return self._placeholder(height)

    @staticmethod
    def _placeholder(height: int) -> List[str]:
        lines = ["Achievements: N/A", "Module unavailable"]
        return lines[:height]


class TimelineWidget(Widget):
    """Session timeline from session_timeline module."""

    name = "Timeline"
    description = "Session event timeline"

    def render(self, width: int, height: int) -> List[str]:
        try:
            from lib.session_timeline import build_timeline, format_compact

            timeline = build_timeline()
            output = format_compact(timeline)
            lines = output.split("\n") if isinstance(output, str) else [str(output)]
            return lines[:height]
        except Exception:
            return self._placeholder(height)

    @staticmethod
    def _placeholder(height: int) -> List[str]:
        lines = ["Timeline: N/A", "Module unavailable"]
        return lines[:height]


class GenomeWidget(Widget):
    """Genome status from converge_dna module."""

    name = "Genome"
    description = "Evolutionary DNA status"

    def render(self, width: int, height: int) -> List[str]:
        try:
            from lib.converge_dna import evolve

            result = evolve(dry_run=True)
            if isinstance(result, dict):
                gen = result.get("generation", "?")
                fitness = result.get("fitness", "?")
                lines = [
                    f"Generation: {gen}",
                    f"Fitness: {fitness}",
                ]
            else:
                lines = [str(result)]
            return lines[:height]
        except Exception:
            return self._placeholder(height)

    @staticmethod
    def _placeholder(height: int) -> List[str]:
        lines = ["Genome: N/A", "Module unavailable"]
        return lines[:height]


# Registry of all built-in widgets
WIDGET_REGISTRY: Dict[str, type] = {
    "weather": WeatherWidget,
    "health": HealthWidget,
    "energy": EnergyWidget,
    "achievements": AchievementWidget,
    "timeline": TimelineWidget,
    "genome": GenomeWidget,
}


@dataclass
class LayoutEngine:
    """Arranges widgets in a grid with bordered boxes."""

    columns: int = 2
    widget_height: int = 6  # content lines per widget
    terminal_width: int = 80
    gap: int = 1  # horizontal gap between columns

    def arrange(self, widgets: List[Widget]) -> List[str]:
        """Arrange widgets into rows of grid columns.

        Returns:
            List of strings forming the complete layout.
        """
        if not widgets:
            return ["(empty dashboard)"]

        col_width = self._column_width()
        rows: List[List[Widget]] = []

        # Chunk widgets into rows
        for i in range(0, len(widgets), self.columns):
            rows.append(widgets[i : i + self.columns])

        output_lines: List[str] = []
        for row in rows:
            row_rendered = self._render_row(row, col_width)
            output_lines.extend(row_rendered)
            output_lines.append("")  # blank line between rows

        # Remove trailing blank
        if output_lines and output_lines[-1] == "":
            output_lines.pop()

        return output_lines

    def _column_width(self) -> int:
        """Calculate width per column."""
        total_gaps = self.gap * max(0, self.columns - 1)
        available = self.terminal_width - total_gaps
        return max(MIN_BOX_WIDTH, available // max(1, self.columns))

    def _render_row(self, widgets: List[Widget], col_width: int) -> List[str]:
        """Render a single row of widgets side-by-side."""
        # Render each widget's box
        boxes = []
        for w in widgets:
            box = w.render_boxed(col_width, self.widget_height)
            boxes.append(box)

        # Normalize box heights
        max_height = max(len(b) for b in boxes) if boxes else 0
        for box in boxes:
            while len(box) < max_height:
                box.append(" " * col_width)

        # Merge side by side
        gap_str = " " * self.gap
        merged = []
        for line_idx in range(max_height):
            parts = []
            for box in boxes:
                line = box[line_idx] if line_idx < len(box) else " " * col_width
                # Pad to col_width
                if len(line) < col_width:
                    line = line + " " * (col_width - len(line))
                parts.append(line)
            merged.append(gap_str.join(parts))

        return merged


@dataclass
class Dashboard:
    """Holds widgets and renders the full dashboard layout."""

    widgets: List[Widget] = field(default_factory=list)
    layout: LayoutEngine = field(default_factory=LayoutEngine)
    title: str = "GRIP Dashboard"

    def add(self, widget: Widget) -> "Dashboard":
        """Add a widget. Returns self for chaining."""
        self.widgets.append(widget)
        return self

    def render(self) -> str:
        """Render the complete dashboard as a string."""
        header = f"=== {self.title} ==="
        lines = [header, ""]
        body = self.layout.arrange(self.widgets)
        lines.extend(body)
        return "\n".join(lines)

    @classmethod
    def from_preset(cls, preset: str, terminal_width: int = 80) -> "Dashboard":
        """Create a dashboard from a named preset.

        Presets:
            full: All 6 widgets in a 3x2 grid
            compact: weather + health + genome in a single row
            custom: Use from_widget_names() instead
        """
        if preset == "full":
            widgets = [cls_() for cls_ in WIDGET_REGISTRY.values()]
            layout = LayoutEngine(columns=3, terminal_width=terminal_width)
        elif preset == "compact":
            widgets = [
                WeatherWidget(),
                HealthWidget(),
                GenomeWidget(),
            ]
            layout = LayoutEngine(columns=3, terminal_width=terminal_width)
        else:
            raise ValueError(f"Unknown preset: {preset!r}. Use 'full', 'compact', or from_widget_names().")

        return cls(widgets=widgets, layout=layout, title=f"GRIP Dashboard ({preset})")

    @classmethod
    def from_widget_names(cls, names: List[str], terminal_width: int = 80) -> "Dashboard":
        """Create a dashboard from a list of widget names."""
        widgets = []
        for name in names:
            key = name.lower().strip()
            if key in WIDGET_REGISTRY:
                widgets.append(WIDGET_REGISTRY[key]())
            else:
                raise ValueError(f"Unknown widget: {name!r}. Available: {list(WIDGET_REGISTRY.keys())}")

        cols = min(len(widgets), 3)
        layout = LayoutEngine(columns=cols, terminal_width=terminal_width)
        return cls(widgets=widgets, layout=layout, title="GRIP Dashboard (custom)")


def get_terminal_width() -> int:
    """Get terminal width, defaulting to 80."""
    try:
        return shutil.get_terminal_size().columns
    except Exception:
        return 80


def list_widgets() -> str:
    """Return a formatted list of available widgets."""
    lines = ["Available widgets:", ""]
    for key, cls_ in WIDGET_REGISTRY.items():
        w = cls_()
        lines.append(f"  {key:15s} {w.description}")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
    parser = argparse.ArgumentParser(
        prog="grip-dashboard",
        description="GRIP Dashboard Composer -- unified terminal dashboard",
    )
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("full", help="Show all widgets in a 3x2 grid")
    sub.add_parser("compact", help="Show weather + health + genome")

    custom_p = sub.add_parser("custom", help="Show custom widget selection")
    custom_p.add_argument(
        "widgets",
        type=str,
        help="Widget names joined with '+' (e.g. weather+health+genome)",
    )

    sub.add_parser("list", help="List available widgets")

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    """CLI entry point."""
    parser = build_parser()
    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        return 0

    term_width = get_terminal_width()

    if args.command == "list":
        print(list_widgets())
        return 0

    if args.command == "full":
        dash = Dashboard.from_preset("full", terminal_width=term_width)
    elif args.command == "compact":
        dash = Dashboard.from_preset("compact", terminal_width=term_width)
    elif args.command == "custom":
        names = [n.strip() for n in args.widgets.split("+") if n.strip()]
        if not names:
            print("Error: no widgets specified", file=sys.stderr)
            return 1
        try:
            dash = Dashboard.from_widget_names(names, terminal_width=term_width)
        except ValueError as e:
            print(f"Error: {e}", file=sys.stderr)
            return 1
    else:
        parser.print_help()
        return 0

    print(dash.render())
    return 0


if __name__ == "__main__":
    sys.exit(main())
