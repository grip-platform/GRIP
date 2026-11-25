#!/usr/bin/env python3
"""RSI queue management — load, save, query, and update candidates."""

import json
from dataclasses import asdict
from datetime import datetime, timezone

from . import config
from .models import Candidate


def load_queue() -> list[Candidate]:
    """Load improvement queue from disk."""
    if not config.QUEUE_FILE.exists():
        return []
    try:
        data = json.loads(config.QUEUE_FILE.read_text())
        return [Candidate(**item) for item in data.get("candidates", [])]
    except (json.JSONDecodeError, TypeError, KeyError):
        return []


def save_queue(candidates: list[Candidate]):
    """Save improvement queue to disk."""
    config.QUEUE_FILE.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "candidates": [asdict(c) for c in candidates],
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "count": len(candidates),
    }
    config.QUEUE_FILE.write_text(json.dumps(data, indent=2))


def get_pending_candidates() -> list[Candidate]:
    """Get candidates sorted by priority score (highest first)."""
    queue = load_queue()
    pending = [c for c in queue if c.status == "pending"]
    return sorted(pending, key=lambda c: c.priority_score, reverse=True)


def mark_candidate(candidate_id: str, status: str, reason: str = ""):
    """Update candidate status in queue."""
    queue = load_queue()
    for c in queue:
        if c.id == candidate_id:
            c.status = status
            if reason:
                c.skip_reason = reason
            break
    save_queue(queue)
