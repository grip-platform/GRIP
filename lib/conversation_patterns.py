#!/usr/bin/env python3
"""Conversation pattern analyzer -- detect recurring session progression patterns.

Scans JSONL session transcript files and identifies recurring tool usage
patterns, topic drift, and session efficiency metrics.
"""

import argparse
import json
import os
import sys
from collections import Counter
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class SessionData:
    """Parsed session transcript."""

    path: str
    messages_count: int = 0
    tool_sequence: list = field(default_factory=list)
    duration_estimate: float = 0.0  # seconds
    has_commit: bool = False
    timestamps: list = field(default_factory=list)
    message_roles: list = field(default_factory=list)


@dataclass
class Pattern:
    """A recurring tool usage n-gram."""

    sequence: tuple
    frequency: int = 0
    avg_position_in_session: float = 0.0
    label: str = ""


# ---------------------------------------------------------------------------
# JSONL scanning
# ---------------------------------------------------------------------------

PROJECTS_DIR = os.path.join(os.environ.get("HOME", ""), ".claude", "projects")

# Tool names that indicate productive work
PRODUCTIVE_TOOLS = {
    "Read", "Edit", "Write", "Bash", "Grep", "Glob",
    "MultiEdit", "TodoRead", "TodoWrite",
}

# Patterns that indicate a commit occurred
COMMIT_INDICATORS = [
    "git commit", "git push", "gh pr create",
    "created commit", "created pull request",
]


def _parse_jsonl_file(path: str) -> list[dict]:
    """Parse a JSONL file into a list of dicts, skipping malformed lines."""
    records = []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except (OSError, IOError):
        pass
    return records


def _extract_tools_from_content(content) -> list[str]:
    """Extract tool names from message content (list of content blocks)."""
    tools = []
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                name = block.get("name", "")
                if name:
                    tools.append(name)
    return tools


def _check_commit_in_content(content) -> bool:
    """Check if content contains commit indicators."""
    text = ""
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    text += block.get("text", "")
            elif isinstance(block, str):
                text += block
    return any(indicator in text.lower() for indicator in COMMIT_INDICATORS)


def _estimate_duration(timestamps: list[str]) -> float:
    """Estimate session duration from first and last timestamp."""
    if len(timestamps) < 2:
        return 0.0
    try:
        from datetime import datetime
        # Parse ISO format timestamps
        first = timestamps[0]
        last = timestamps[-1]
        # Strip trailing Z and handle fractional seconds
        for suffix in ("Z", "+00:00"):
            first = first.replace(suffix, "")
            last = last.replace(suffix, "")
        fmt = "%Y-%m-%dT%H:%M:%S"
        # Handle fractional seconds by truncating
        if "." in first:
            first = first[:first.index(".")]
        if "." in last:
            last = last[:last.index(".")]
        t0 = datetime.strptime(first, fmt)
        t1 = datetime.strptime(last, fmt)
        return max((t1 - t0).total_seconds(), 0.0)
    except (ValueError, ImportError):
        return 0.0


def load_session(path: str) -> SessionData:
    """Load a single JSONL session into a SessionData object."""
    records = _parse_jsonl_file(path)
    session = SessionData(path=path)
    timestamps = []
    tools = []
    has_commit = False

    for record in records:
        msg = record.get("message", {})
        if not isinstance(msg, dict):
            continue

        role = msg.get("role", "")
        content = msg.get("content", "")
        rec_type = record.get("type", "")

        # Count user and assistant messages
        if role in ("user", "assistant"):
            session.messages_count += 1
            session.message_roles.append(role)

        # Extract tool uses from assistant content
        if role == "assistant":
            extracted = _extract_tools_from_content(content)
            tools.extend(extracted)
            if _check_commit_in_content(content):
                has_commit = True

        # Check user messages for commit evidence too
        if role == "user" and isinstance(content, str):
            if _check_commit_in_content(content):
                has_commit = True

        # Collect timestamps
        ts = record.get("timestamp", "")
        if ts:
            timestamps.append(ts)

        # Check tool result content for commit evidence
        if rec_type == "tool_result" or record.get("type") == "tool_result":
            result_content = record.get("content", "")
            if isinstance(result_content, str) and any(
                ind in result_content.lower() for ind in COMMIT_INDICATORS
            ):
                has_commit = True
            # Also check nested message content for tool results
            if not has_commit and isinstance(msg, dict):
                msg_content = msg.get("content", "")
                if isinstance(msg_content, str) and any(
                    ind in msg_content.lower() for ind in COMMIT_INDICATORS
                ):
                    has_commit = True

    session.tool_sequence = tools
    session.has_commit = has_commit
    session.timestamps = timestamps
    session.duration_estimate = _estimate_duration(timestamps)
    return session


def scan_sessions(
    max_sessions: int = 20,
    projects_dir: Optional[str] = None,
) -> list[SessionData]:
    """Load recent sessions sorted by mtime (most recent first).

    Only loads top-level session files (not subagent transcripts).
    """
    base = projects_dir or PROJECTS_DIR
    if not os.path.isdir(base):
        return []

    # Collect all top-level JSONL files (not in subagents/)
    jsonl_files = []
    for root, dirs, files in os.walk(base):
        # Skip subagent directories
        if "subagents" in root:
            continue
        for fname in files:
            if fname.endswith(".jsonl"):
                full = os.path.join(root, fname)
                jsonl_files.append(full)

    # Sort by modification time, newest first
    jsonl_files.sort(key=lambda p: os.path.getmtime(p), reverse=True)

    # Load up to max_sessions
    sessions = []
    for path in jsonl_files[:max_sessions]:
        session = load_session(path)
        if session.messages_count > 0:
            sessions.append(session)

    return sessions


# ---------------------------------------------------------------------------
# Pattern extraction
# ---------------------------------------------------------------------------

def extract_tool_sequences(session: SessionData) -> list[tuple]:
    """Extract ordered tool usage n-grams (length 2-4) from a session."""
    tools = session.tool_sequence
    if len(tools) < 2:
        return []

    ngrams = []
    for n in range(2, 5):  # 2, 3, 4
        for i in range(len(tools) - n + 1):
            ngram = tuple(tools[i : i + n])
            ngrams.append(ngram)
    return ngrams


def _label_pattern(sequence: tuple) -> str:
    """Auto-label a tool sequence pattern using heuristics."""
    seq_str = " -> ".join(sequence)
    names = [s.lower() for s in sequence]

    # Read -> Edit -> Bash = fix pattern
    if len(sequence) >= 3:
        for i in range(len(sequence) - 2):
            if (
                sequence[i] == "Read"
                and sequence[i + 1] == "Edit"
                and sequence[i + 2] == "Bash"
            ):
                return "fix"

    # Glob -> Grep -> Read = explore pattern
    if len(sequence) >= 3:
        for i in range(len(sequence) - 2):
            if (
                sequence[i] == "Glob"
                and sequence[i + 1] == "Grep"
                and sequence[i + 2] == "Read"
            ):
                return "explore"

    # Edit -> Bash where Bash likely runs tests = test pattern
    if len(sequence) >= 2:
        for i in range(len(sequence) - 1):
            if sequence[i] == "Edit" and sequence[i + 1] == "Bash":
                return "test"

    # Read -> Read -> Read = deep read pattern
    if all(s == "Read" for s in sequence) and len(sequence) >= 2:
        return "deep read"

    # Grep -> Read = targeted read
    if len(sequence) >= 2:
        for i in range(len(sequence) - 1):
            if sequence[i] == "Grep" and sequence[i + 1] == "Read":
                return "targeted read"

    # Glob -> Read = scan pattern
    if len(sequence) >= 2:
        for i in range(len(sequence) - 1):
            if sequence[i] == "Glob" and sequence[i + 1] == "Read":
                return "scan"

    # Write -> Bash = create-and-run
    if len(sequence) >= 2:
        for i in range(len(sequence) - 1):
            if sequence[i] == "Write" and sequence[i + 1] == "Bash":
                return "create-and-run"

    return "mixed"


def detect_patterns(sessions: list[SessionData]) -> list[Pattern]:
    """Find recurring tool sequences across sessions.

    Returns patterns sorted by frequency (descending).
    """
    ngram_counter: Counter = Counter()
    ngram_positions: dict[tuple, list[float]] = {}

    for session in sessions:
        tools = session.tool_sequence
        if len(tools) < 2:
            continue

        total_tools = len(tools)
        seen_in_session = set()

        for n in range(2, 5):
            for i in range(len(tools) - n + 1):
                ngram = tuple(tools[i : i + n])
                # Count each unique n-gram once per session for frequency
                if ngram not in seen_in_session:
                    ngram_counter[ngram] += 1
                    seen_in_session.add(ngram)

                # Track position (normalized 0-1)
                position = i / max(total_tools - 1, 1)
                ngram_positions.setdefault(ngram, []).append(position)

    # Build Pattern objects for n-grams appearing in 2+ sessions
    patterns = []
    for ngram, freq in ngram_counter.items():
        if freq < 2:
            continue
        positions = ngram_positions.get(ngram, [])
        avg_pos = sum(positions) / len(positions) if positions else 0.0
        label = _label_pattern(ngram)
        patterns.append(
            Pattern(
                sequence=ngram,
                frequency=freq,
                avg_position_in_session=round(avg_pos, 3),
                label=label,
            )
        )

    # Sort by frequency descending, then by sequence length descending
    patterns.sort(key=lambda p: (-p.frequency, -len(p.sequence)))
    return patterns


# ---------------------------------------------------------------------------
# Drift detection
# ---------------------------------------------------------------------------

def detect_drift(session: SessionData) -> list[str]:
    """Identify potential topic switches without completion markers.

    Heuristic: a long stretch of explore-type tools (Glob/Grep/Read)
    followed by another explore stretch without an intervening commit
    indicator (Edit->Bash or Write) suggests a topic switch.
    """
    warnings = []
    tools = session.tool_sequence
    if len(tools) < 4:
        return warnings

    EXPLORE_TOOLS = {"Read", "Glob", "Grep"}
    ACTION_TOOLS = {"Edit", "Write", "Bash"}

    # Track segments: consecutive explore runs separated by action runs
    explore_segments = 0
    in_explore = False
    had_action_since_last_explore = False
    segment_start_indices = []

    for i, tool in enumerate(tools):
        if tool in EXPLORE_TOOLS:
            if not in_explore:
                in_explore = True
                if not had_action_since_last_explore and explore_segments > 0:
                    warnings.append(
                        f"Possible topic switch at tool #{i}: "
                        f"new exploration without completing previous action"
                    )
                explore_segments += 1
                segment_start_indices.append(i)
                had_action_since_last_explore = False
        elif tool in ACTION_TOOLS:
            in_explore = False
            had_action_since_last_explore = True

    # Check for sessions without commits despite many tool calls
    if len(tools) > 15 and not session.has_commit:
        warnings.append(
            f"Session has {len(tools)} tool calls but no commit detected"
        )

    # Check for repeated exploration without convergence
    if explore_segments >= 3 and not session.has_commit:
        warnings.append(
            f"{explore_segments} exploration segments without a commit"
        )

    return warnings


# ---------------------------------------------------------------------------
# Efficiency
# ---------------------------------------------------------------------------

def session_efficiency(session: SessionData) -> float:
    """Calculate ratio of productive tool calls to total messages.

    Productive = tool calls that directly manipulate code/files.
    Total = all messages (user + assistant).
    """
    if session.messages_count == 0:
        return 0.0

    productive = sum(1 for t in session.tool_sequence if t in PRODUCTIVE_TOOLS)
    return round(productive / session.messages_count, 3)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _format_pattern(p: Pattern, index: int) -> str:
    """Format a single pattern for display."""
    seq = " -> ".join(p.sequence)
    return (
        f"  {index}. [{p.label}] {seq}\n"
        f"     Frequency: {p.frequency} sessions | "
        f"Avg position: {p.avg_position_in_session:.1%} through session"
    )


def run_analyze(args: argparse.Namespace) -> dict:
    """Run the analyze command and return results dict."""
    sessions = scan_sessions(
        max_sessions=args.last,
        projects_dir=args.projects_dir,
    )

    if not sessions:
        result = {
            "sessions_analyzed": 0,
            "patterns": [],
            "efficiency": {},
            "drift_warnings": [],
        }
        if not args.json:
            print("No sessions found.")
        return result

    patterns = detect_patterns(sessions)
    top_patterns = patterns[:5]

    # Efficiency stats
    efficiencies = [session_efficiency(s) for s in sessions]
    avg_eff = sum(efficiencies) / len(efficiencies) if efficiencies else 0.0

    # Drift warnings (from most recent 5 sessions)
    drift_warnings = []
    for s in sessions[:5]:
        warnings = detect_drift(s)
        if warnings:
            drift_warnings.append({
                "session": os.path.basename(s.path),
                "warnings": warnings,
            })

    # Session stats
    commit_rate = (
        sum(1 for s in sessions if s.has_commit) / len(sessions)
        if sessions
        else 0.0
    )
    avg_tools = (
        sum(len(s.tool_sequence) for s in sessions) / len(sessions)
        if sessions
        else 0.0
    )

    result = {
        "sessions_analyzed": len(sessions),
        "avg_tools_per_session": round(avg_tools, 1),
        "commit_rate": round(commit_rate, 3),
        "avg_efficiency": round(avg_eff, 3),
        "patterns": [
            {
                "sequence": list(p.sequence),
                "frequency": p.frequency,
                "avg_position": p.avg_position_in_session,
                "label": p.label,
            }
            for p in top_patterns
        ],
        "drift_warnings": drift_warnings,
    }

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"Analyzed {len(sessions)} sessions")
        print(f"  Avg tools/session: {avg_tools:.1f}")
        print(f"  Commit rate: {commit_rate:.0%}")
        print(f"  Avg efficiency: {avg_eff:.1%}")
        print()

        if top_patterns:
            print("Top patterns:")
            for i, p in enumerate(top_patterns, 1):
                print(_format_pattern(p, i))
            print()

        if drift_warnings:
            print("Drift warnings (recent sessions):")
            for dw in drift_warnings:
                print(f"  {dw['session']}:")
                for w in dw["warnings"]:
                    print(f"    - {w}")
            print()

    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Detect recurring patterns in session progression",
    )
    sub = parser.add_subparsers(dest="command")

    analyze = sub.add_parser("analyze", help="Analyze session patterns")
    analyze.add_argument(
        "--last",
        type=int,
        default=20,
        help="Number of recent sessions to analyze (default: 20)",
    )
    analyze.add_argument(
        "--json",
        action="store_true",
        help="Machine-readable JSON output",
    )
    analyze.add_argument(
        "--projects-dir",
        default=None,
        help="Override projects directory (for testing)",
    )

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return 1

    if args.command == "analyze":
        run_analyze(args)

    return 0


if __name__ == "__main__":
    sys.exit(main())
