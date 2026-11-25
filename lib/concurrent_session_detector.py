#!/usr/bin/env python3
"""
Concurrent Session Detector - Cross-session awareness for GRIP anti-drift.

Detects other active GRIP sessions working on the same or different projects,
enabling "What's Up Next" to account for parallel work and avoid duplicate
recommendations (e.g. "create PR" when another session already did).

Architecture:
    Imports scan_all_sessions() from session_lister.py (DRY). Filters out the
    calling session and formats results for injection into anti-drift output
    or session-start hook.

Usage:
    # From Python (import)
    from concurrent_session_detector import detect_siblings, format_sibling_summary

    siblings = detect_siblings(exclude_session_id="abc-123")
    if siblings:
        print(format_sibling_summary(siblings))

    # From CLI
    grip-py lib/concurrent_session_detector.py                    # All other sessions
    grip-py lib/concurrent_session_detector.py --exclude abc-123  # Exclude specific session
    grip-py lib/concurrent_session_detector.py --exclude-pid 1234 # Exclude by PID
    grip-py lib/concurrent_session_detector.py --project .claude  # Filter by project
    grip-py lib/concurrent_session_detector.py --format brief     # One-line summary
    grip-py lib/concurrent_session_detector.py --format hook      # Hook injection format
    grip-py lib/concurrent_session_detector.py --json             # JSON output

Version: 1.2.0
"""

import json
import os
import sys
from pathlib import Path
from typing import Optional


# One scan for every caller: session_lister's, which judges a recorded PID the
# right way on Windows too (GRIP#8817). This module used to carry a second copy of
# that scan, for a session_lister that could not be imported. The copy asked
# os.kill(pid, 0), which on Windows is a console Ctrl+C call and not a check, and
# it deleted the lock of every session it could not signal (GRIP#8883). Without
# session_lister this module now fails to import, and says so.
_LIB_DIR = Path(__file__).parent
sys.path.insert(0, str(_LIB_DIR))

from session_lister import scan_all_sessions  # noqa: E402


def detect_siblings(
    exclude_session_id: Optional[str] = None,
    exclude_pid: Optional[int] = None,
    project_filter: Optional[str] = None,
) -> list[dict]:
    """Detect concurrent sessions, excluding the caller.

    Args:
        exclude_session_id: Session ID to exclude (the calling session).
        exclude_pid: PID to exclude (alternative to session_id).
        project_filter: If set, only return sessions matching this project name.

    Returns:
        List of sibling session dicts, sorted by uptime descending.
    """
    all_sessions = scan_all_sessions()

    siblings = []
    for session in all_sessions:
        # Exclude the calling session
        if exclude_session_id and session.get("session_id") == exclude_session_id:
            continue
        if exclude_pid and session.get("pid") == exclude_pid:
            continue

        # Optional project filter
        if project_filter:
            name = session.get("project_name", "")
            if project_filter.lower() not in name.lower():
                continue

        siblings.append(session)

    # Sort by uptime descending (longest running first)
    siblings.sort(key=lambda s: s.get("uptime_minutes", 0), reverse=True)
    return siblings


def _format_brief(siblings: list[dict]) -> str:
    """One compact line, for hook integration."""
    parts = [f"{s.get('branch', '?')} (pid {s.get('pid', 0)})" for s in siblings]
    return f"[CONCURRENT: {len(siblings)} other session(s) | {', '.join(parts)}]"


def _format_hook(siblings: list[dict]) -> str:
    """Several lines, for injection by a hook."""
    lines = [f"[CONCURRENT SESSIONS: {len(siblings)} sibling(s) detected]"]
    for s in siblings:
        sid_short = s.get("session_id", "")[:8]
        branch = s.get("branch", "?")
        project = s.get("project_name", "?")
        uptime = s.get("uptime_display", "?")
        desc = s.get("label", "") or s.get("achievement", "") or "no description"
        if len(desc) > 60:
            desc = desc[:57] + "..."
        lines.append(f"  {sid_short} | {project}/{branch} | {uptime} | {desc}")
    lines.append("NOTE: Check what siblings are doing before recommending duplicate work.")
    return "\n".join(lines)


def _format_full(siblings: list[dict]) -> str:
    """Markdown, for anti-drift output. The default format."""
    lines = [f"## Concurrent Sessions ({len(siblings)} sibling(s))", ""]
    for s in siblings:
        sid_short = s.get("session_id", "")[:8]
        branch = s.get("branch", "?")
        project = s.get("project_name", "?")
        uptime = s.get("uptime_display", "?")
        label = s.get("label", "")
        achievement = s.get("achievement", "")
        gen = s.get("generation", 0)
        lines.append(f"- **{sid_short}** on `{project}/{branch}` ({uptime})")
        if label:
            lines.append(f"  Label: {label}")
        if achievement:
            lines.append(f"  Last: {achievement}")
        if gen:
            lines.append(f"  Gen: {gen}")
    lines.append("")
    lines.append(
        "**Warning**: Verify sibling work before recommending PRs, merges, or branch operations."
    )
    return "\n".join(lines)


_FORMATTERS = {"brief": _format_brief, "hook": _format_hook}


def format_sibling_summary(siblings: list[dict], fmt: str = "full") -> str:
    """Format sibling sessions for display.

    Args:
        siblings: List of sibling session dicts.
        fmt: Output format - "full", "brief", or "hook". Any other value gives "full".

    Returns:
        Formatted string. Empty string if no siblings.
    """
    if not siblings:
        return ""
    return _FORMATTERS.get(fmt, _format_full)(siblings)


def _resolve_current_session_id() -> str:
    """Resolve the current session's ID from env or breadcrumb."""
    env_id = os.environ.get("CLAUDE_SESSION_ID", "")
    if env_id:
        return env_id

    # Try breadcrumb file
    ppid = os.getppid()
    from _compat import grip_sessions_dir
    breadcrumb = Path(grip_sessions_dir()) / f"sid-{ppid}"
    if breadcrumb.exists():
        try:
            return breadcrumb.read_text().strip()
        except OSError:
            pass

    return ""


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Detect concurrent GRIP sessions for anti-drift awareness"
    )
    parser.add_argument(
        "--exclude",
        help="Session ID to exclude (defaults to auto-detected current session)",
    )
    parser.add_argument(
        "--exclude-pid", type=int, help="PID to exclude"
    )
    parser.add_argument(
        "--project", help="Filter by project name substring"
    )
    parser.add_argument(
        "--format",
        choices=["full", "brief", "hook"],
        default="full",
        help="Output format (default: full)",
    )
    parser.add_argument(
        "--json", action="store_true", help="Output as JSON"
    )

    args = parser.parse_args()

    # Auto-detect current session if not excluded explicitly
    exclude_sid = args.exclude
    if not exclude_sid and not args.exclude_pid:
        exclude_sid = _resolve_current_session_id()

    siblings = detect_siblings(
        exclude_session_id=exclude_sid,
        exclude_pid=args.exclude_pid,
        project_filter=args.project,
    )

    if args.json:
        print(json.dumps(siblings, indent=2))
    else:
        output = format_sibling_summary(siblings, fmt=args.format)
        if output:
            print(output)
        elif args.format != "brief":
            print("No concurrent sessions detected.")


if __name__ == "__main__":
    main()
