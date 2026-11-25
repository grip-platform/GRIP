#!/usr/bin/env python3
"""
Auto-Memory Saver for GRIP

Automatically saves semantic memory snapshots at regular token intervals
to prevent context loss. Non-blocking background operation.

Adapted from Cortex by Tomas Hjertefolger (MIT License).
https://github.com/hjertefolger/cortex

Usage:
    Called by context-gate-hook.py at soft checkpoints.
    Can also be run manually: python3 auto-memory-saver.py <session_id>
"""

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from json_utils import safe_load_json

# Path-portability shim (rules/path-portability.md): CLAUDE_DIR resolves to
# $CLAUDE_DIR env (when set) or $HOME/.claude.
_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))
from claude_dir import get_claude_dir  # noqa: E402

CLAUDE_DIR = get_claude_dir()
STATE_FILE = CLAUDE_DIR / "state" / "memory" / "auto-memory-state.json"

# Configuration
SNAPSHOT_INTERVAL_TOKENS = 10000  # ~1% of 1M context
MAX_SNAPSHOTS_PER_SESSION = 10


def get_state() -> dict:
    """Load auto-memory state."""
    return safe_load_json(STATE_FILE, {"sessions": {}})


def save_state(state: dict):
    """Save auto-memory state atomically."""
    try:
        # Try to use atomic_write if available
        sys.path.insert(0, str(CLAUDE_DIR / "lib"))
        from atomic_write import atomic_write_json
        atomic_write_json(STATE_FILE, state)
    except ImportError:
        # Fallback to regular write
        STATE_FILE.write_text(json.dumps(state, indent=2))


def estimate_tokens(text: str) -> int:
    """Rough token estimate: 1 token ≈ 4 characters."""
    return len(text) // 4


def should_snapshot(session_id: str, current_tokens: int) -> bool:
    """Check if we should create a new snapshot."""
    state = get_state()
    session_state = state.get("sessions", {}).get(session_id, {})

    last_snapshot_tokens = session_state.get("last_snapshot_tokens", 0)
    snapshot_count = session_state.get("snapshot_count", 0)

    # Check if enough tokens have passed
    if current_tokens - last_snapshot_tokens < SNAPSHOT_INTERVAL_TOKENS:
        return False

    # Check if we haven't hit max snapshots
    if snapshot_count >= MAX_SNAPSHOTS_PER_SESSION:
        return False

    return True



def store_content_in_kono(
    content: str, session_id: str = "", project_path: str = "",
    namespace: str = "",
) -> bool:
    """Store checkpoint content in KONO as episodic memory for cross-session recall.

    Takes raw content (transcript lines), extracts a meaningful summary,
    and stores it directly in KONO. No intermediate file needed.

    ``namespace`` (opt-in, V>> 2026-05-21 P2a): when set, the row is stored
    under that namespace instead of the default org namespace. Test fixtures
    should pass ``namespace='test:integration'`` so their probe content is
    structurally excluded from default recall (``_resolve_accessible_namespaces``
    only resolves operator-org + legacy + merged namespaces, never ``test:*``).
    Empty/None preserves the previous default behaviour for every existing
    production caller.
    """
    try:
        sys.path.insert(0, str(CLAUDE_DIR / "lib"))
        from kono_substrate import MemorySubstrate

        if not content or len(content) < 50:
            return False

        # Extract a meaningful summary from the raw transcript
        summary = content[:300].strip()
        if len(content) > 300:
            summary += "..."

        substrate = MemorySubstrate()
        mem_content = "[checkpoint] {}".format(summary)
        substrate.store_session_memory(
            content=mem_content,
            memory_type="episodic",
            project_path=project_path if project_path else None,
            namespace=namespace if namespace else None,
        )
        return True

    except Exception as e:
        print(f"Failed to store checkpoint in KONO: {e}", file=sys.stderr)
        return False


def _derive_project_path(transcript_path: str) -> str:
    """Derive original project path from transcript location.

    Transcripts live at ~/.claude/projects/{encoded-path}/{session}.jsonl
    The encoded path uses the formula: path.replace('/', '-').replace('.', '-')
    We can't perfectly reverse this, but the encoded dir name is sufficient
    for KONO project_path matching.
    """
    try:
        tp = Path(transcript_path)
        # Parent is the project dir (e.g. -Users-foo--claude)
        project_dir = tp.parent.name
        return project_dir
    except Exception:
        return ""



def auto_save(session_id: str, transcript_path: str = None) -> bool:
    """
    Main entry point for auto-memory saving.

    Reads the last 100 lines of the conversation transcript, stores a
    checkpoint summary in KONO for cross-session recall.

    Returns True if a checkpoint was stored.
    """
    # Read transcript if provided
    content = ""
    if transcript_path and Path(transcript_path).exists():
        try:
            with open(transcript_path, 'r') as f:
                lines = f.readlines()[-100:]  # Last 100 JSONL entries
            content = "\n".join(lines)
        except Exception:
            pass

    current_tokens = estimate_tokens(content)

    if not should_snapshot(session_id, current_tokens):
        return False

    if not content or len(content) < 50:
        return False

    # Derive project path from transcript path (transcript lives in project dir)
    project_path = ""
    if transcript_path:
        project_path = _derive_project_path(transcript_path)

    # Store directly in KONO (no intermediate JSON files)
    stored = store_content_in_kono(
        content=content,
        session_id=session_id,
        project_path=project_path,
    )

    if stored:
        # Update state tracking
        state = get_state()
        if "sessions" not in state:
            state["sessions"] = {}
        if session_id not in state["sessions"]:
            state["sessions"][session_id] = {}
        state["sessions"][session_id]["last_snapshot_tokens"] = current_tokens
        state["sessions"][session_id]["snapshot_count"] = (
            state["sessions"][session_id].get("snapshot_count", 0) + 1
        )
        state["sessions"][session_id]["last_snapshot_time"] = (
            datetime.now(timezone.utc).isoformat()
        )
        save_state(state)
        print(f"[AUTO-MEMORY] Checkpoint stored in KONO for session {session_id}", file=sys.stderr)

    return stored


def main():
    """CLI interface."""
    import argparse

    parser = argparse.ArgumentParser(description="Auto-memory snapshot saver")
    parser.add_argument("session_id", help="Session ID")
    parser.add_argument("--transcript", "-t", help="Path to transcript JSONL")
    parser.add_argument("--force", "-f", action="store_true", help="Force snapshot")

    args = parser.parse_args()

    if args.force:
        # Force a checkpoint regardless of interval
        content = ""
        if args.transcript and Path(args.transcript).exists():
            with open(args.transcript, 'r') as f:
                lines = f.readlines()[-100:]  # Last 100 JSONL entries
            content = "\n".join(lines)

        project_path = _derive_project_path(args.transcript) if args.transcript else ""
        stored = store_content_in_kono(
            content=content,
            session_id=args.session_id,
            project_path=project_path,
        )
        if stored:
            print(f"Checkpoint stored in KONO for session {args.session_id}")
        else:
            print("No content to store (transcript empty or too short)")
    else:
        result = auto_save(args.session_id, args.transcript)
        if result:
            print("Snapshot created")
        else:
            print("No snapshot needed (interval not reached)")


if __name__ == "__main__":
    main()
