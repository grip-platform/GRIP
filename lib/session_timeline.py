#!/usr/bin/env python3
"""Session Timeline -- ASCII timeline of key session events.

Converts Moment objects from session_replay into visual ASCII timelines
showing commits, PRs, errors, directives, mode switches, and tool milestones
plotted against time.

Usage:
    python3 lib/session_timeline.py plot [session_path]
    python3 lib/session_timeline.py plot --last 1
    python3 lib/session_timeline.py compact [session_path]
    Add --json for machine-readable output.
    Add --width N to control timeline width (default 80).
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class TimelineEvent:
    position: float     # 0.0-1.0, relative position in session
    symbol: str         # single char
    label: str          # max 30 chars
    moment_type: str    # commit | pr | error | mode_switch | directive | tool

    def __post_init__(self) -> None:
        self.position = max(0.0, min(1.0, self.position))
        self.label = self.label[:30]
        self.symbol = self.symbol[:1] if self.symbol else "?"


@dataclass
class Timeline:
    events: list[TimelineEvent] = field(default_factory=list)
    duration_minutes: float = 0.0
    start_time: str = ""
    end_time: str = ""


# ---------------------------------------------------------------------------
# Symbol mapping
# ---------------------------------------------------------------------------

SYMBOL_MAP: dict[str, str] = {
    "commit": "C",
    "pr": "P",
    "error": "E",
    "mode_switch": "M",
    "directive": ">",
    "tool": "*",
    "tool_milestone": "*",
}

DEFAULT_SYMBOL = "?"


def _symbol_for(moment_type: str) -> str:
    """Return the timeline symbol for a moment type."""
    return SYMBOL_MAP.get(moment_type, DEFAULT_SYMBOL)


# ---------------------------------------------------------------------------
# Timeline building
# ---------------------------------------------------------------------------

def _parse_timestamp(ts: str) -> Optional[datetime]:
    """Parse an ISO timestamp string, returning None on failure."""
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def build_timeline(
    moments: list, duration_minutes: float | None = None
) -> Timeline:
    """Convert Moment objects to a Timeline with normalized positions.

    Args:
        moments: List of Moment objects (from session_replay).
        duration_minutes: Session duration. If None, estimated from timestamps.

    Returns:
        Timeline with events positioned 0.0-1.0.
    """
    if not moments:
        return Timeline()

    # Parse all timestamps
    parsed: list[tuple[datetime | None, object]] = []
    for m in moments:
        dt = _parse_timestamp(getattr(m, "timestamp", ""))
        parsed.append((dt, m))

    # Determine time bounds
    valid_times = [dt for dt, _ in parsed if dt is not None]
    if len(valid_times) >= 2:
        first_dt = min(valid_times)
        last_dt = max(valid_times)
        span_seconds = (last_dt - first_dt).total_seconds()
    elif len(valid_times) == 1:
        first_dt = valid_times[0]
        last_dt = valid_times[0]
        span_seconds = 0.0
    else:
        first_dt = None
        last_dt = None
        span_seconds = 0.0

    # Duration
    if duration_minutes is not None:
        actual_duration = duration_minutes
    elif span_seconds > 0:
        actual_duration = span_seconds / 60.0
    else:
        actual_duration = 0.0

    # Start/end time strings
    start_time = first_dt.strftime("%H:%M") if first_dt else "00:00"
    end_time = last_dt.strftime("%H:%M") if last_dt else "00:00"

    # Build events with positions
    events: list[TimelineEvent] = []
    for i, (dt, m) in enumerate(parsed):
        if dt is not None and span_seconds > 0 and first_dt is not None:
            pos = (dt - first_dt).total_seconds() / span_seconds
        elif len(parsed) > 1:
            # Fallback: distribute evenly
            pos = i / (len(parsed) - 1)
        else:
            pos = 0.5

        content = getattr(m, "content", "")
        mtype = getattr(m, "moment_type", "")
        events.append(TimelineEvent(
            position=pos,
            symbol=_symbol_for(mtype),
            label=content[:30],
            moment_type=mtype,
        ))

    return Timeline(
        events=events,
        duration_minutes=actual_duration,
        start_time=start_time,
        end_time=end_time,
    )


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

def _format_time_label(minutes: float) -> str:
    """Format minutes as HH:MM relative."""
    h = int(minutes) // 60
    m = int(minutes) % 60
    return f"{h:02d}:{m:02d}"


def format_timeline(timeline: Timeline, width: int = 80) -> str:
    """Render a Timeline as an ASCII visualization.

    Args:
        timeline: Timeline object to render.
        width: Total character width (minimum 40).

    Returns:
        Multi-line ASCII string.
    """
    width = max(40, width)

    if not timeline.events:
        return "(no events)"

    start = timeline.start_time or "00:00"
    end = timeline.end_time or "00:00"
    mid_minutes = timeline.duration_minutes / 2.0
    mid = _format_time_label(mid_minutes)

    # Time ruler line
    # Layout: "HH:MM " + dashes + " HH:MM " + dashes + " HH:MM"
    # We reserve space for three time labels and padding
    label_space = 5  # "HH:MM"
    # Available dash space
    inner_width = width - (label_space * 3) - 4  # 4 spaces between labels
    if inner_width < 4:
        inner_width = 4
    left_dashes = inner_width // 2
    right_dashes = inner_width - left_dashes

    ruler = (
        f"{start} "
        + "\u2500" * left_dashes
        + f" {mid} "
        + "\u2500" * right_dashes
        + f" {end}"
    )

    # Event line: place symbols on a track of spaces
    # Track width matches the ruler (from after start label to before end label)
    track_width = len(ruler)
    # Usable positions: offset past start label
    track_start = len(start) + 1
    track_end = track_width - len(end) - 1
    usable = track_end - track_start

    event_chars = [" "] * track_width
    for ev in timeline.events:
        col = track_start + int(ev.position * max(usable - 1, 0))
        col = max(track_start, min(col, track_end - 1))
        event_chars[col] = ev.symbol

    event_line = "".join(event_chars)

    # Legend
    legend_lines: list[str] = []
    for ev in timeline.events:
        legend_lines.append(f"{ev.symbol} {ev.moment_type}: {ev.label}")

    lines = [ruler, event_line, ""]
    lines.extend(legend_lines)
    return "\n".join(lines)


def format_compact(timeline: Timeline) -> str:
    """Single-line summary of a timeline.

    Example: "03:00 | 5C 2E 1P 1> | 45.0m"
    """
    if not timeline.events:
        return "(no events)"

    counts: dict[str, int] = {}
    for ev in timeline.events:
        counts[ev.symbol] = counts.get(ev.symbol, 0) + 1

    parts = []
    # Order: C, P, E, M, >, *
    for sym in ["C", "P", "E", "M", ">", "*", "?"]:
        if sym in counts:
            parts.append(f"{counts[sym]}{sym}")

    start = timeline.start_time or "00:00"
    duration = f"{timeline.duration_minutes:.1f}m"
    symbols = " ".join(parts) if parts else "none"

    return f"{start} | {symbols} | {duration}"


# ---------------------------------------------------------------------------
# Convenience loader
# ---------------------------------------------------------------------------

def load_and_plot(session_path: str, width: int = 80) -> str:
    """Load a session file, extract moments, build and format timeline.

    Imports session_replay at call time to avoid hard dependency at module level.
    """
    # Import session_replay from the same lib directory
    lib_dir = str(Path(__file__).resolve().parent)
    if lib_dir not in sys.path:
        sys.path.insert(0, lib_dir)

    from session_replay import parse_session, extract_moments  # type: ignore

    messages = parse_session(session_path)
    moments = extract_moments(messages)
    timeline = build_timeline(moments)
    return format_timeline(timeline, width=width)


# ---------------------------------------------------------------------------
# Session discovery (reuse from session_replay)
# ---------------------------------------------------------------------------

def _discover_sessions() -> list[str]:
    """Find session JSONL files, sorted by mtime descending."""
    import glob
    import os

    claude_dir = os.environ.get(
        "CLAUDE_DIR", os.path.join(str(Path.home()), ".claude")
    )
    pattern = os.path.join(claude_dir, "projects", "*", "*.jsonl")
    paths = glob.glob(pattern)
    paths.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    return paths


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Session Timeline -- ASCII timeline of session events.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Machine-readable JSON output",
    )
    parser.add_argument(
        "--width",
        type=int,
        default=80,
        help="Timeline width in characters (default: 80)",
    )
    sub = parser.add_subparsers(dest="command")

    # plot
    plot_p = sub.add_parser("plot", help="Plot ASCII timeline of a session")
    plot_p.add_argument(
        "session_path", nargs="?", help="Path to session JSONL file"
    )
    plot_p.add_argument(
        "--last", type=int, default=0, help="Plot last N sessions (default: 1)"
    )

    # compact
    compact_p = sub.add_parser("compact", help="One-line timeline summary")
    compact_p.add_argument(
        "session_path", nargs="?", help="Path to session JSONL file"
    )
    compact_p.add_argument(
        "--last", type=int, default=0, help="Summarize last N sessions"
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        return 1

    # Resolve session paths
    paths: list[str] = []
    session_path = getattr(args, "session_path", None)
    last_n = getattr(args, "last", 0)

    if last_n > 0:
        paths = _discover_sessions()[:last_n]
        if not paths:
            print("No sessions found.", file=sys.stderr)
            return 1
    elif session_path:
        paths = [session_path]
    else:
        print("Provide a session path or use --last N.", file=sys.stderr)
        return 1

    # Import session_replay
    lib_dir = str(Path(__file__).resolve().parent)
    if lib_dir not in sys.path:
        sys.path.insert(0, lib_dir)

    from session_replay import parse_session, extract_moments  # type: ignore

    for sp in paths:
        messages = parse_session(sp)
        moments = extract_moments(messages)
        timeline = build_timeline(moments)

        if args.json_output:
            data = asdict(timeline)
            data["source"] = sp
            print(json.dumps(data, indent=2))
        elif args.command == "plot":
            print(f"Session: {sp}")
            print(format_timeline(timeline, width=args.width))
            print()
        elif args.command == "compact":
            label = Path(sp).stem[:30]
            print(f"{label}: {format_compact(timeline)}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
