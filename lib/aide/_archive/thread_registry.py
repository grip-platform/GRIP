"""
Copilot Thread Registry - tracks threads originated by Copilot.

Storage: ~/.claude/data/aide/threads.jsonl
Format: One JSON object per line, append-only with periodic compaction.

Entry schema:
{
    "thread_ts": "1234567890.123456",
    "channel_id": "C123ABC",
    "origin": "briefing|digest|ritual|notification",
    "ritual_id": "morning-brief",  # optional
    "context": { ... },            # original context
    "created_at": "2026-02-16T07:00:00Z",
    "ttl_hours": 48,
}
"""

from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, List, Any
import json
import logging

logger = logging.getLogger(__name__)

THREADS_FILE = Path.home() / ".claude" / "data" / "aide" / "threads.jsonl"
DEFAULT_TTL_HOURS = 48


class CopilotThreadRegistry:
    """Registry for Copilot-originated Slack threads."""

    @classmethod
    def register(
        cls,
        channel_id: str,
        thread_ts: str,
        origin: str,
        ritual_id: Optional[str] = None,
        context: Optional[Dict] = None,
        ttl_hours: int = DEFAULT_TTL_HOURS,
    ) -> bool:
        """Register a Copilot thread for future context restoration."""
        entry = {
            "thread_ts": thread_ts,
            "channel_id": channel_id,
            "origin": origin,
            "ritual_id": ritual_id,
            "context": context or {},
            "created_at": datetime.now(timezone.utc).isoformat(),
            "ttl_hours": ttl_hours,
        }

        from .atomic_io import atomic_append_jsonl
        try:
            atomic_append_jsonl(THREADS_FILE, entry)
            logger.info("Registered Copilot thread: %s in %s", thread_ts, channel_id)
            return True
        except Exception as e:
            logger.warning("Failed to register thread: %s", e)
            return False

    @classmethod
    def lookup(cls, channel_id: str, thread_ts: str) -> Optional[Dict]:
        """Look up a registered thread, returns None if not found or expired."""
        from .atomic_io import read_jsonl

        now = datetime.now(timezone.utc)

        for entry in read_jsonl(THREADS_FILE):
            if entry.get("channel_id") == channel_id and entry.get("thread_ts") == thread_ts:
                # Check TTL
                created_str = entry.get("created_at", "2000-01-01T00:00:00+00:00")
                try:
                    created = datetime.fromisoformat(created_str)
                except ValueError:
                    continue
                ttl = timedelta(hours=entry.get("ttl_hours", DEFAULT_TTL_HOURS))
                if now - created > ttl:
                    return None  # Expired
                return entry

        return None

    @classmethod
    def is_copilot_thread(cls, channel_id: str, thread_ts: str) -> bool:
        """Quick check if a thread is Copilot-originated."""
        return cls.lookup(channel_id, thread_ts) is not None

    @classmethod
    def cleanup_expired(cls) -> int:
        """Remove expired entries. Call periodically."""
        from .atomic_io import read_jsonl, locked_read_modify_write

        now = datetime.now(timezone.utc)
        entries = read_jsonl(THREADS_FILE)
        active = []
        removed = 0

        for entry in entries:
            created_str = entry.get("created_at", "2000-01-01T00:00:00+00:00")
            try:
                created = datetime.fromisoformat(created_str)
            except ValueError:
                removed += 1
                continue
            ttl = timedelta(hours=entry.get("ttl_hours", DEFAULT_TTL_HOURS))
            if now - created <= ttl:
                active.append(entry)
            else:
                removed += 1

        if removed > 0:
            locked_read_modify_write(THREADS_FILE, active)
            logger.info("Cleaned up %d expired thread entries", removed)

        return removed
