#!/usr/bin/env python3
"""Session Replay -- extract and summarize key moments from past sessions.

Parses Claude Code session JSONL files and identifies commits, PRs, errors,
user directives, mode switches, and tool milestones.

Usage:
    python3 lib/session_replay.py replay [session_path]
    python3 lib/session_replay.py replay --last 3
    python3 lib/session_replay.py highlights [session_path]
    python3 lib/session_replay.py compare path_a path_b
    Add --json for machine-readable output.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class Moment:
    timestamp: str
    moment_type: str  # commit | error | directive | tool | pr | mode_switch
    content: str      # brief summary, max 100 chars
    importance: float  # 0.0 - 1.0

    def __post_init__(self) -> None:
        self.content = self.content[:100]


@dataclass
class SessionSummary:
    path: str
    total_messages: int
    duration_estimate: str
    moments: list[Moment] = field(default_factory=list)
    top_tools: list[str] = field(default_factory=list)
    commits: int = 0
    errors: int = 0


# ---------------------------------------------------------------------------
# Importance scores
# ---------------------------------------------------------------------------

IMPORTANCE = {
    "pr": 1.0,
    "commit": 0.9,
    "error": 0.7,
    "directive": 0.6,
    "mode_switch": 0.5,
    "tool": 0.3,
}


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def parse_session(path: str | Path) -> list[dict]:
    """Load JSONL, skip malformed lines."""
    messages: list[dict] = []
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    messages.append(json.loads(line))
                except (json.JSONDecodeError, ValueError):
                    continue
    except OSError:
        pass
    return messages


# ---------------------------------------------------------------------------
# Moment extraction helpers
# ---------------------------------------------------------------------------

_DIRECTIVE_VERBS = re.compile(
    r"^\s*(?:/\w+\s+)?(?:fix|build|add|implement|create|update|refactor|remove|"
    r"delete|deploy|test|write|change|move|rename|run|push|merge|revert|undo|"
    r"ship|enable|disable)\b",
    re.IGNORECASE,
)

_COMMIT_PATTERN = re.compile(r"git commit", re.IGNORECASE)
_PR_PATTERN = re.compile(
    r"gh pr create|https://github\.com/[^\s]+/pull/\d+", re.IGNORECASE
)
_MODE_PATTERN = re.compile(r"^/mode\s+", re.IGNORECASE)
_ERROR_WORDS = re.compile(
    r"(?i)(?:\berror\b|\bfailed\b|\bfailure\b|traceback|exception|ENOENT|"
    r"permission denied|command not found|no such file)",
)


def _extract_user_text(msg: dict) -> Optional[str]:
    """Extract plain text from a user message."""
    message = msg.get("message", {})
    if not isinstance(message, dict):
        return None
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(item.get("text", ""))
        return " ".join(parts) if parts else None
    return None


def _extract_assistant_content_blocks(msg: dict) -> list[dict]:
    """Return content blocks from an assistant message."""
    message = msg.get("message", {})
    if not isinstance(message, dict):
        return []
    content = message.get("content", [])
    if isinstance(content, list):
        return content
    return []


def _extract_tool_results(msg: dict) -> list[dict]:
    """Return tool_result blocks from a user message."""
    message = msg.get("message", {})
    if not isinstance(message, dict):
        return []
    content = message.get("content", [])
    if not isinstance(content, list):
        return []
    return [
        c for c in content
        if isinstance(c, dict) and c.get("type") == "tool_result"
    ]


def _tool_result_text(result: dict) -> str:
    """Extract text content from a tool_result block."""
    content = result.get("content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(item.get("text", ""))
        return " ".join(parts)
    return str(content)


def extract_moments(messages: list[dict]) -> list[Moment]:
    """Scan messages for key events and return Moment objects."""
    moments: list[Moment] = []
    tool_counts: dict[str, int] = {}
    tool_milestones_hit: set[str] = set()
    read_count = 0
    total_tool_calls = 0

    for msg in messages:
        msg_type = msg.get("type", "")
        timestamp = msg.get("timestamp", "")

        # --- User messages: directives, mode switches, tool results ---
        if msg_type == "user":
            text = _extract_user_text(msg)
            if text:
                # Mode switch
                if _MODE_PATTERN.search(text):
                    mode_name = text.strip().split(None, 2)
                    label = mode_name[1] if len(mode_name) > 1 else "unknown"
                    moments.append(Moment(
                        timestamp=timestamp,
                        moment_type="mode_switch",
                        content=f"Mode switch: {label}",
                        importance=IMPORTANCE["mode_switch"],
                    ))
                # Directive
                elif _DIRECTIVE_VERBS.match(text):
                    moments.append(Moment(
                        timestamp=timestamp,
                        moment_type="directive",
                        content=text.strip().replace("\n", " ")[:100],
                        importance=IMPORTANCE["directive"],
                    ))

            # Tool results in user messages (commits, PRs, errors)
            for result in _extract_tool_results(msg):
                result_text = _tool_result_text(result)
                is_error = result.get("is_error", False)

                if is_error and _ERROR_WORDS.search(result_text):
                    moments.append(Moment(
                        timestamp=timestamp,
                        moment_type="error",
                        content=result_text.strip().replace("\n", " ")[:100],
                        importance=IMPORTANCE["error"],
                    ))

                if _COMMIT_PATTERN.search(result_text):
                    moments.append(Moment(
                        timestamp=timestamp,
                        moment_type="commit",
                        content=result_text.strip().replace("\n", " ")[:100],
                        importance=IMPORTANCE["commit"],
                    ))

                if _PR_PATTERN.search(result_text):
                    moments.append(Moment(
                        timestamp=timestamp,
                        moment_type="pr",
                        content=result_text.strip().replace("\n", " ")[:100],
                        importance=IMPORTANCE["pr"],
                    ))

        # --- Assistant messages: tool_use blocks ---
        if msg_type == "assistant":
            for block in _extract_assistant_content_blocks(msg):
                if not isinstance(block, dict):
                    continue
                if block.get("type") != "tool_use":
                    continue

                tool_name = block.get("name", "unknown")
                tool_counts[tool_name] = tool_counts.get(tool_name, 0) + 1
                total_tool_calls += 1

                if tool_name == "Read":
                    read_count += 1

                # Bash tool_use: check command for commits/PRs
                if tool_name == "Bash":
                    cmd = block.get("input", {}).get("command", "")
                    if _COMMIT_PATTERN.search(cmd):
                        moments.append(Moment(
                            timestamp=timestamp,
                            moment_type="commit",
                            content=f"Bash: {cmd[:90]}",
                            importance=IMPORTANCE["commit"],
                        ))
                    if _PR_PATTERN.search(cmd):
                        moments.append(Moment(
                            timestamp=timestamp,
                            moment_type="pr",
                            content=f"Bash: {cmd[:90]}",
                            importance=IMPORTANCE["pr"],
                        ))

                # Tool milestones
                if total_tool_calls in (100, 200, 500, 1000):
                    milestone_key = f"tool_total_{total_tool_calls}"
                    if milestone_key not in tool_milestones_hit:
                        tool_milestones_hit.add(milestone_key)
                        moments.append(Moment(
                            timestamp=timestamp,
                            moment_type="tool",
                            content=f"Tool milestone: {total_tool_calls} total tool calls",
                            importance=IMPORTANCE["tool"],
                        ))

                if read_count in (50, 100, 200):
                    read_milestone_key = f"read_{read_count}"
                    if read_milestone_key not in tool_milestones_hit:
                        tool_milestones_hit.add(read_milestone_key)
                        moments.append(Moment(
                            timestamp=timestamp,
                            moment_type="tool",
                            content=f"Tool milestone: {read_count} file reads",
                            importance=IMPORTANCE["tool"],
                        ))

    return moments


# ---------------------------------------------------------------------------
# Summarization
# ---------------------------------------------------------------------------

def _estimate_duration(messages: list[dict]) -> str:
    """Estimate session duration from first and last timestamps."""
    timestamps = [m.get("timestamp", "") for m in messages if m.get("timestamp")]
    if len(timestamps) < 2:
        return "unknown"
    try:
        first = timestamps[0].replace("Z", "+00:00")
        last = timestamps[-1].replace("Z", "+00:00")
        dt_a = datetime.fromisoformat(first)
        dt_b = datetime.fromisoformat(last)
        total_seconds = int((dt_b - dt_a).total_seconds())
        if total_seconds < 0:
            return "unknown"
        hours, remainder = divmod(total_seconds, 3600)
        minutes, _ = divmod(remainder, 60)
        if hours:
            return f"{hours}h {minutes}m"
        return f"{minutes}m"
    except (ValueError, TypeError):
        return "unknown"


def _count_tools(messages: list[dict]) -> dict[str, int]:
    """Count tool usage across the session."""
    counts: dict[str, int] = {}
    for msg in messages:
        if msg.get("type") != "assistant":
            continue
        for block in _extract_assistant_content_blocks(msg):
            if isinstance(block, dict) and block.get("type") == "tool_use":
                name = block.get("name", "unknown")
                counts[name] = counts.get(name, 0) + 1
    return counts


def summarize_session(path: str | Path) -> SessionSummary:
    """Build a full session summary from a JSONL file."""
    messages = parse_session(path)
    moments = extract_moments(messages)
    tool_counts = _count_tools(messages)
    top_tools = sorted(tool_counts, key=lambda k: tool_counts[k], reverse=True)[:10]

    commit_count = sum(1 for m in moments if m.moment_type == "commit")
    error_count = sum(1 for m in moments if m.moment_type == "error")

    return SessionSummary(
        path=str(path),
        total_messages=len(messages),
        duration_estimate=_estimate_duration(messages),
        moments=moments,
        top_tools=top_tools,
        commits=commit_count,
        errors=error_count,
    )


def highlight_reel(path: str | Path, top_n: int = 10) -> list[Moment]:
    """Return the most important moments sorted by importance descending."""
    messages = parse_session(path)
    moments = extract_moments(messages)
    moments.sort(key=lambda m: m.importance, reverse=True)
    return moments[:top_n]


def compare_sessions(path_a: str | Path, path_b: str | Path) -> dict:
    """Compare two sessions side-by-side."""
    summary_a = summarize_session(path_a)
    summary_b = summarize_session(path_b)

    return {
        "session_a": {
            "path": summary_a.path,
            "messages": summary_a.total_messages,
            "duration": summary_a.duration_estimate,
            "top_tools": summary_a.top_tools,
            "commits": summary_a.commits,
            "errors": summary_a.errors,
            "error_rate": round(
                summary_a.errors / max(summary_a.total_messages, 1), 4
            ),
            "moment_count": len(summary_a.moments),
        },
        "session_b": {
            "path": summary_b.path,
            "messages": summary_b.total_messages,
            "duration": summary_b.duration_estimate,
            "top_tools": summary_b.top_tools,
            "commits": summary_b.commits,
            "errors": summary_b.errors,
            "error_rate": round(
                summary_b.errors / max(summary_b.total_messages, 1), 4
            ),
            "moment_count": len(summary_b.moments),
        },
    }


# ---------------------------------------------------------------------------
# Session discovery
# ---------------------------------------------------------------------------

def _discover_sessions() -> list[str]:
    """Find all session JSONL files, sorted by mtime descending."""
    claude_dir = os.environ.get(
        "CLAUDE_DIR", os.path.join(str(Path.home()), ".claude")
    )
    pattern = os.path.join(claude_dir, "projects", "*", "*.jsonl")
    paths = glob.glob(pattern)
    paths.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    return paths


# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------

def _format_moment(m: Moment) -> str:
    ts = m.timestamp[:19] if m.timestamp else "no-timestamp"
    return f"  [{ts}] ({m.moment_type:12s} | imp={m.importance:.1f}) {m.content}"


def _print_summary(summary: SessionSummary, as_json: bool = False) -> None:
    if as_json:
        print(json.dumps(asdict(summary), indent=2))
        return

    print(f"Session: {summary.path}")
    print(
        f"Messages: {summary.total_messages}  "
        f"Duration: {summary.duration_estimate}"
    )
    print(f"Commits: {summary.commits}  Errors: {summary.errors}")
    if summary.top_tools:
        print(f"Top tools: {', '.join(summary.top_tools)}")
    print(f"\nTimeline ({len(summary.moments)} moments):")
    for m in summary.moments:
        print(_format_moment(m))


def _print_highlights(
    moments: list[Moment], path: str, as_json: bool = False
) -> None:
    if as_json:
        print(json.dumps([asdict(m) for m in moments], indent=2))
        return

    print(f"Highlights for: {path}")
    print(f"Top {len(moments)} moments by importance:\n")
    for i, m in enumerate(moments, 1):
        ts = m.timestamp[:19] if m.timestamp else "no-timestamp"
        print(
            f"  {i:2d}. [{ts}] {m.moment_type:12s} "
            f"(imp={m.importance:.1f}) {m.content}"
        )


def _print_comparison(result: dict, as_json: bool = False) -> None:
    if as_json:
        print(json.dumps(result, indent=2))
        return

    for label in ("session_a", "session_b"):
        s = result[label]
        print(f"\n{'=' * 60}")
        print(f"  {label}: {s['path']}")
        print(f"  Messages: {s['messages']}  Duration: {s['duration']}")
        print(
            f"  Commits: {s['commits']}  Errors: {s['errors']}  "
            f"Error rate: {s['error_rate']}"
        )
        print(f"  Top tools: {', '.join(s['top_tools'])}")
        print(f"  Moments: {s['moment_count']}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Session Replay -- extract key moments from Claude Code sessions.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Machine-readable JSON output",
    )
    sub = parser.add_subparsers(dest="command")

    # replay
    replay_p = sub.add_parser("replay", help="Full timeline of a session")
    replay_p.add_argument(
        "session_path", nargs="?", help="Path to session JSONL file"
    )
    replay_p.add_argument(
        "--last", type=int, default=0, help="Replay last N sessions"
    )

    # highlights
    hl_p = sub.add_parser("highlights", help="Top moments by importance")
    hl_p.add_argument("session_path", help="Path to session JSONL file")
    hl_p.add_argument(
        "--top", type=int, default=10, help="Number of top moments"
    )

    # compare
    cmp_p = sub.add_parser("compare", help="Compare two sessions")
    cmp_p.add_argument("path_a", help="First session JSONL")
    cmp_p.add_argument("path_b", help="Second session JSONL")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        return 1

    if args.command == "replay":
        if args.last > 0:
            sessions = _discover_sessions()[: args.last]
            if not sessions:
                print("No sessions found.", file=sys.stderr)
                return 1
            for sp in sessions:
                summary = summarize_session(sp)
                _print_summary(summary, as_json=args.json_output)
                if not args.json_output:
                    print()
        else:
            if not args.session_path:
                print(
                    "Provide a session path or use --last N.", file=sys.stderr
                )
                return 1
            summary = summarize_session(args.session_path)
            _print_summary(summary, as_json=args.json_output)

    elif args.command == "highlights":
        moments = highlight_reel(args.session_path, top_n=args.top)
        _print_highlights(moments, args.session_path, as_json=args.json_output)

    elif args.command == "compare":
        result = compare_sessions(args.path_a, args.path_b)
        _print_comparison(result, as_json=args.json_output)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
