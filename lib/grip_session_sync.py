#!/usr/bin/env python3
"""
grip_session_sync.py — File-based session sync for concurrent Claude sessions.

Stage 2 of Universal Session Sync. Concurrent Claude Code sessions share context
automatically via a shared ~/.claude/sync/ directory — no human mediation needed.

Each session writes its transfer payload to sync/{session_id}.md on milestones.
Other sessions read peer files to gain cross-session context. Stale files (>2h)
are cleaned automatically.

Usage:
    python3 lib/grip_session_sync.py write     # Write own sync file
    python3 lib/grip_session_sync.py read      # Read peer contexts
    python3 lib/grip_session_sync.py merge     # Output merged context
    python3 lib/grip_session_sync.py clean     # Remove stale files
    python3 lib/grip_session_sync.py status    # Show sync status

Called by: /save integration, /sync command
"""

import argparse
import hashlib
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", Path.home() / ".claude"))
LIB_DIR = CLAUDE_DIR / "lib"
SYNC_DIR = CLAUDE_DIR / "sync"
STALE_THRESHOLD_SECONDS = 7200  # 2 hours

if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))


# ---------------------------------------------------------------------------
# Core helper
# ---------------------------------------------------------------------------

def _safe(fn):
    """Call fn(), return None on any error."""
    try:
        return fn()
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Session identity
# ---------------------------------------------------------------------------

def current_session_id() -> str:
    """Stable session ID from PID + parent PID.

    Uses parent PID (the claude process) combined with own PID
    to produce a short 8-char hex identifier.
    """
    raw = f"{os.getppid()}-{os.getpid()}"
    return hashlib.sha256(raw.encode()).hexdigest()[:8]


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------

def write_sync(
    session_id: Optional[str] = None,
    project_path: Optional[str] = None,
) -> dict:
    """Write current session's transfer payload to sync directory.

    Reuses build_transfer_payload() from grip_transfer.py for the payload.
    Returns {"ok": bool, "path": str, "size": int}.
    """
    sid = session_id or current_session_id()
    SYNC_DIR.mkdir(exist_ok=True)

    try:
        from grip_transfer import build_transfer_payload
        payload = build_transfer_payload(project_path=project_path)
    except ImportError:
        payload = _fallback_payload()

    target = SYNC_DIR / f"{sid}.md"
    try:
        target.write_text(payload, encoding="utf-8")
        return {"ok": True, "path": str(target), "size": len(payload)}
    except OSError as e:
        return {"ok": False, "path": str(target), "size": 0, "error": str(e)}


def _fallback_payload() -> str:
    """Minimal payload when grip_transfer is unavailable."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    ctx_path = CLAUDE_DIR / "rules" / "session-context.md"
    if ctx_path.exists():
        try:
            return ctx_path.read_text(encoding="utf-8")
        except OSError:
            pass
    return f"# Session Sync\n\nGenerated: {now}\n\nNo context available."


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------

def read_peers(session_id: Optional[str] = None) -> list[dict]:
    """Read all peer sessions' sync files, excluding own.

    Returns list of dicts: {"session_id": str, "content": str, "mtime": float}.
    Runs stale cleanup first.
    """
    _cleanup_stale()
    sid = session_id or current_session_id()

    if not SYNC_DIR.exists():
        return []

    peers = []
    for f in SYNC_DIR.glob("*.md"):
        if f.stem == sid:
            continue
        try:
            peers.append({
                "session_id": f.stem,
                "content": f.read_text(encoding="utf-8"),
                "mtime": f.stat().st_mtime,
            })
        except OSError:
            continue

    # Most recently modified first
    peers.sort(key=lambda p: p["mtime"], reverse=True)
    return peers


# ---------------------------------------------------------------------------
# Merge
# ---------------------------------------------------------------------------

def _flatten_peer_sections(peers: list[dict]) -> list[tuple[str, str]]:
    """Flatten all peer contents into a single list of (heading, body) tuples.

    Peers must be pre-sorted most-recent-first. Returns tuples in that order
    so callers can deduplicate with first-seen-wins.
    """
    import itertools
    return list(itertools.chain.from_iterable(
        _parse_sections(p["content"]).items() for p in peers
    ))


def _dedup_sections(flat: list[tuple[str, str]]) -> dict[str, str]:
    """Deduplicate (heading, body) tuples — first-seen wins. O(n)."""
    result: dict[str, str] = {}
    for heading, body in flat:
        result.setdefault(heading, body)
    return result


def merge_context(session_id: Optional[str] = None) -> str:
    """Merge all peer session contexts into a unified markdown view.

    Deduplicates by ## heading, keeping the most recently modified version.
    Peers arrive pre-sorted most-recent-first from read_peers(), so
    first-seen-wins gives O(n) deduplication after a flat chain.
    """
    peers = read_peers(session_id)
    if not peers:
        return "No peer sessions found."

    sections = _dedup_sections(_flatten_peer_sections(peers))

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    parts = [f"# Merged Session Context\n\nGenerated: {now} | Peers: {len(peers)}\n"]
    parts.extend(f"## {heading}\n\n{body}" for heading, body in sections.items())
    return "\n\n".join(parts)


def _parse_sections(text: str) -> dict[str, str]:
    """Parse markdown into {heading: body} dict, splitting on ## headings."""
    sections: dict[str, str] = {}
    current_heading = ""
    current_lines: list[str] = []

    for line in text.splitlines():
        if line.startswith("## "):
            if current_heading:
                sections[current_heading] = "\n".join(current_lines).strip()
            current_heading = line[3:].strip()
            current_lines = []
        elif current_heading:
            current_lines.append(line)

    if current_heading:
        sections[current_heading] = "\n".join(current_lines).strip()

    return sections


# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------

def _cleanup_stale() -> int:
    """Delete sync files older than STALE_THRESHOLD_SECONDS. Returns count."""
    if not SYNC_DIR.exists():
        return 0

    now = time.time()
    cleaned = 0
    for f in SYNC_DIR.glob("*.md"):
        try:
            age = now - f.stat().st_mtime
            if age > STALE_THRESHOLD_SECONDS:
                f.unlink()
                cleaned += 1
        except OSError:
            continue
    return cleaned


def remove_own(session_id: Optional[str] = None) -> bool:
    """Remove own sync file (session-end cleanup). Returns True if removed."""
    sid = session_id or current_session_id()
    target = SYNC_DIR / f"{sid}.md"
    try:
        if target.exists():
            target.unlink()
            return True
    except OSError:
        pass
    return False


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------

def status(session_id: Optional[str] = None) -> dict:
    """Return sync status: own file info + peer count + stale count."""
    sid = session_id or current_session_id()
    own_file = SYNC_DIR / f"{sid}.md"

    own_exists = own_file.exists()
    own_size = 0
    own_age = 0.0
    if own_exists:
        try:
            stat = own_file.stat()
            own_size = stat.st_size
            own_age = time.time() - stat.st_mtime
        except OSError:
            pass

    peers = read_peers(sid)
    stale = _cleanup_stale()

    return {
        "session_id": sid,
        "own_file_exists": own_exists,
        "own_file_size": own_size,
        "own_file_age_seconds": round(own_age, 1),
        "peer_count": len(peers),
        "peer_ids": [p["session_id"] for p in peers],
        "stale_cleaned": stale,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="GRIP Session Sync — file-based cross-session context sharing",
    )
    parser.add_argument(
        "command", choices=["write", "read", "merge", "clean", "status"],
        help="Action to perform",
    )
    parser.add_argument(
        "--project", type=str, default=None,
        help="Override project path (default: CWD)",
    )
    parser.add_argument(
        "--session-id", type=str, default=None,
        help="Override session ID (default: auto-generated)",
    )
    return parser.parse_args()


_COMMANDS = {
    "write": lambda args: _cmd_write(args),
    "read": lambda args: _cmd_read(args),
    "merge": lambda args: _cmd_merge(args),
    "clean": lambda args: _cmd_clean(args),
    "status": lambda args: _cmd_status(args),
}


def _cmd_write(args: argparse.Namespace) -> None:
    result = write_sync(session_id=args.session_id, project_path=args.project)
    if result["ok"]:
        print(f"Written: {result['path']} ({result['size']:,} chars)")
    else:
        print(f"Failed: {result.get('error', 'unknown')}", file=sys.stderr)
        sys.exit(1)


def _cmd_read(args: argparse.Namespace) -> None:
    peers = read_peers(session_id=args.session_id)
    if not peers:
        print("No peer sessions found.")
        return
    for p in peers:
        age = time.time() - p["mtime"]
        print(f"--- Session {p['session_id']} ({age:.0f}s ago, {len(p['content']):,} chars) ---")
        print(p["content"][:500])
        if len(p["content"]) > 500:
            print(f"  ... ({len(p['content']) - 500:,} chars truncated)")
        print()


def _cmd_merge(args: argparse.Namespace) -> None:
    print(merge_context(session_id=args.session_id))


def _cmd_clean(args: argparse.Namespace) -> None:
    cleaned = _cleanup_stale()
    print(f"Cleaned {cleaned} stale sync file{'s' if cleaned != 1 else ''}.")


def _cmd_status(args: argparse.Namespace) -> None:
    s = status(session_id=args.session_id)
    print(json.dumps(s, indent=2))


def main() -> None:
    args = _parse_args()
    _COMMANDS[args.command](args)


if __name__ == "__main__":
    main()
