#!/usr/bin/env python3
"""Unified multi-format export for all GRIP data.

Supports JSON, CSV, Markdown, HTML, and plain text output
from any GRIP data source (converge metrics, genome, sessions, etc.).

Data input is always list[dict] -- each dict is a row with string keys.

CLI usage:
    python3 lib/export_formatter.py export <source> --format <fmt> [--output <path>]
    python3 lib/export_formatter.py formats
    python3 lib/export_formatter.py sources
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, date
from enum import Enum
from html import escape as html_escape
from pathlib import Path
from typing import Any, Optional


class ExportFormat(Enum):
    """Supported export formats."""

    JSON = "json"
    CSV = "csv"
    MARKDOWN = "markdown"
    HTML = "html"
    TEXT = "text"


class ExportSource(Enum):
    """Known GRIP data sources."""

    CONVERGE_METRICS = "converge_metrics"
    GENOME = "genome"
    SESSIONS = "sessions"
    SPRINT_REPORT = "sprint_report"
    MODULE_REGISTRY = "module_registry"
    ACHIEVEMENTS = "achievements"


# Default paths for each source (relative to GRIP root)
SOURCE_PATHS: dict[str, str] = {
    ExportSource.CONVERGE_METRICS.value: "data/converge-metrics.json",
    ExportSource.GENOME.value: "data/genome.json",
    ExportSource.SESSIONS.value: "data/sessions.json",
    ExportSource.SPRINT_REPORT.value: "data/sprint-report.json",
    ExportSource.MODULE_REGISTRY.value: "data/module-registry.json",
    ExportSource.ACHIEVEMENTS.value: "data/achievements.json",
}


@dataclass
class ExportConfig:
    """Configuration for a data export operation."""

    source: ExportSource
    format: ExportFormat
    output_path: Optional[str] = None
    filters: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Value formatting
# ---------------------------------------------------------------------------

MAX_STR_LEN = 60


def format_value(value: Any) -> str:
    """Smart formatting for cell values.

    - Truncates long strings with ellipsis
    - Formats floats to 2 decimal places
    - Formats datetime/date objects as ISO strings
    - Converts None to empty string
    - Converts booleans to Yes/No
    """
    if value is None:
        return ""
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, float):
        return f"{value:.2f}"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (list, dict)):
        s = json.dumps(value, default=str)
        if len(s) > MAX_STR_LEN:
            return s[: MAX_STR_LEN - 3] + "..."
        return s
    s = str(value)
    if len(s) > MAX_STR_LEN:
        return s[: MAX_STR_LEN - 3] + "..."
    return s


# ---------------------------------------------------------------------------
# Header detection
# ---------------------------------------------------------------------------


def detect_headers(data: list[dict]) -> list[str]:
    """Extract ordered column headers from a list of dicts.

    Preserves insertion order, collects the union of all keys.
    """
    seen: dict[str, None] = {}
    for row in data:
        for key in row:
            if key not in seen:
                seen[key] = None
    return list(seen.keys())


# ---------------------------------------------------------------------------
# Format converters
# ---------------------------------------------------------------------------


def to_json(data: list[dict], pretty: bool = True) -> str:
    """Export data as JSON string."""
    indent = 2 if pretty else None
    return json.dumps(data, indent=indent, default=str, ensure_ascii=False)


def to_csv(data: list[dict], headers: list[str] | None = None) -> str:
    """Export data as CSV string.

    Headers are auto-detected from dict keys if not provided.
    """
    if not data:
        return ""
    if headers is None:
        headers = detect_headers(data)
    buf = io.StringIO()
    writer = csv.DictWriter(
        buf,
        fieldnames=headers,
        extrasaction="ignore",
        lineterminator="\n",
    )
    writer.writeheader()
    for row in data:
        formatted = {k: format_value(row.get(k)) for k in headers}
        writer.writerow(formatted)
    return buf.getvalue()


def to_markdown_table(data: list[dict], headers: list[str] | None = None) -> str:
    """Export data as a Markdown table."""
    if not data:
        return ""
    if headers is None:
        headers = detect_headers(data)

    # Build formatted rows
    rows: list[list[str]] = []
    for row in data:
        rows.append([format_value(row.get(h)) for h in headers])

    # Column widths (minimum header length)
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            if i < len(widths):
                widths[i] = max(widths[i], len(cell))

    def pad_row(cells: list[str]) -> str:
        parts = [cell.ljust(widths[i]) for i, cell in enumerate(cells)]
        return "| " + " | ".join(parts) + " |"

    lines = [
        pad_row(headers),
        "| " + " | ".join("-" * w for w in widths) + " |",
    ]
    for row in rows:
        lines.append(pad_row(row))

    return "\n".join(lines)


def to_html_table(data: list[dict], title: str = "GRIP Export") -> str:
    """Export data as an HTML table with basic CSS."""
    if not data:
        return f"<html><head><title>{html_escape(title)}</title></head><body><p>No data.</p></body></html>"

    headers = detect_headers(data)

    css = (
        "body{font-family:system-ui,sans-serif;margin:2rem}"
        "table{border-collapse:collapse;width:100%}"
        "th,td{border:1px solid #ddd;padding:8px;text-align:left}"
        "th{background:#f5f5f5;font-weight:600}"
        "tr:nth-child(even){background:#fafafa}"
        "h1{font-size:1.4rem}"
    )

    parts = [
        "<!DOCTYPE html>",
        "<html>",
        "<head>",
        f"<title>{html_escape(title)}</title>",
        f"<style>{css}</style>",
        "</head>",
        "<body>",
        f"<h1>{html_escape(title)}</h1>",
        "<table>",
        "<thead><tr>",
    ]
    for h in headers:
        parts.append(f"<th>{html_escape(h)}</th>")
    parts.append("</tr></thead>")
    parts.append("<tbody>")
    for row in data:
        parts.append("<tr>")
        for h in headers:
            val = format_value(row.get(h))
            parts.append(f"<td>{html_escape(val)}</td>")
        parts.append("</tr>")
    parts.append("</tbody>")
    parts.append("</table>")
    parts.append("</body>")
    parts.append("</html>")

    return "\n".join(parts)


def to_text(data: list[dict]) -> str:
    """Export data as plain text with aligned columns."""
    if not data:
        return ""
    headers = detect_headers(data)
    rows: list[list[str]] = []
    for row in data:
        rows.append([format_value(row.get(h)) for h in headers])

    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            if i < len(widths):
                widths[i] = max(widths[i], len(cell))

    def fmt_row(cells: list[str]) -> str:
        return "  ".join(cell.ljust(widths[i]) for i, cell in enumerate(cells))

    lines = [fmt_row(headers), "  ".join("-" * w for w in widths)]
    for row in rows:
        lines.append(fmt_row(row))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main dispatcher
# ---------------------------------------------------------------------------

_FORMAT_MAP = {
    ExportFormat.JSON: lambda data, **kw: to_json(data, pretty=kw.get("pretty", True)),
    ExportFormat.CSV: lambda data, **kw: to_csv(data, headers=kw.get("headers")),
    ExportFormat.MARKDOWN: lambda data, **kw: to_markdown_table(
        data, headers=kw.get("headers")
    ),
    ExportFormat.HTML: lambda data, **kw: to_html_table(
        data, title=kw.get("title", "GRIP Export")
    ),
    ExportFormat.TEXT: lambda data, **kw: to_text(data),
}


def export_data(data: list[dict], fmt: ExportFormat, **kwargs: Any) -> str:
    """Format data in the specified export format.

    Args:
        data: List of dicts (rows).
        fmt: Target ExportFormat.
        **kwargs: Format-specific options (pretty, headers, title).

    Returns:
        Formatted string.
    """
    converter = _FORMAT_MAP.get(fmt)
    if converter is None:
        raise ValueError(f"Unsupported format: {fmt}")
    return converter(data, **kwargs)


def list_formats() -> list[str]:
    """Return available format names."""
    return [f.value for f in ExportFormat]


def list_sources() -> list[str]:
    """Return available data source names."""
    return [s.value for s in ExportSource]


# ---------------------------------------------------------------------------
# Data loading helpers
# ---------------------------------------------------------------------------


def _grip_root() -> Path:
    """Resolve GRIP root directory."""
    env = os.environ.get("GRIP_ROOT")
    if env:
        return Path(env)
    return Path(os.environ.get("HOME", "")) / ".claude"


def _load_source(source: ExportSource, filters: dict[str, Any] | None = None) -> list[dict]:
    """Load data from a GRIP source file and apply optional filters."""
    rel = SOURCE_PATHS.get(source.value)
    if rel is None:
        raise ValueError(f"No default path for source: {source.value}")
    path = _grip_root() / rel
    if not path.exists():
        raise FileNotFoundError(f"Source file not found: {path}")
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, dict):
        # Try common wrapper keys
        for key in ("data", "items", "rows", "entries", "results", "records"):
            if key in raw and isinstance(raw[key], list):
                raw = raw[key]
                break
        else:
            raw = [raw]
    if not isinstance(raw, list):
        raw = [raw]
    # Ensure each element is a dict
    data: list[dict] = []
    for item in raw:
        if isinstance(item, dict):
            data.append(item)
        else:
            data.append({"value": item})
    # Apply filters
    if filters:
        for fkey, fval in filters.items():
            data = [row for row in data if str(row.get(fkey, "")) == str(fval)]
    return data


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="export_formatter",
        description="GRIP multi-format data exporter",
    )
    sub = parser.add_subparsers(dest="command")

    # export
    exp = sub.add_parser("export", help="Export a data source")
    exp.add_argument("source", choices=list_sources(), help="Data source name")
    exp.add_argument(
        "--format",
        "-f",
        choices=list_formats(),
        default="json",
        help="Output format (default: json)",
    )
    exp.add_argument("--output", "-o", help="Output file path (default: stdout)")
    exp.add_argument(
        "--filter",
        action="append",
        metavar="KEY=VALUE",
        help="Filter rows (repeatable)",
    )

    # formats
    sub.add_parser("formats", help="List available formats")

    # sources
    sub.add_parser("sources", help="List available data sources")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "formats":
        for f in list_formats():
            print(f)
        return 0

    if args.command == "sources":
        for s in list_sources():
            print(s)
        return 0

    if args.command == "export":
        source = ExportSource(args.source)
        fmt = ExportFormat(args.format)
        filters: dict[str, Any] = {}
        if args.filter:
            for filt in args.filter:
                if "=" not in filt:
                    print(f"Invalid filter (expected KEY=VALUE): {filt}", file=sys.stderr)
                    return 1
                k, v = filt.split("=", 1)
                filters[k] = v
        try:
            data = _load_source(source, filters)
        except FileNotFoundError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
        output = export_data(data, fmt)
        if args.output:
            Path(args.output).parent.mkdir(parents=True, exist_ok=True)
            Path(args.output).write_text(output, encoding="utf-8")
            print(f"Exported to {args.output}")
        else:
            print(output)
        return 0

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
