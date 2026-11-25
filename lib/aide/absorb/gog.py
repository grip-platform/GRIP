"""Google Workspace absorber (gog) — Gmail, Calendar, Drive.

Wraps existing CLI helpers (``lib.gcal``, ``lib.gog_gmail``, ``lib.gog_drive``)
rather than duplicating their auth flow. If those tools are unreachable
(no DWD creds in this environment), the functions return zero records but
do not raise — same shape, no surprise crashes during overnight runs.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from typing import Any, Callable, Dict, List, Optional

from .base import AbsorbRecord, coalesce_actors, make_id, utc_now, write_record


def _run(cmd: List[str], timeout_s: int = 60) -> Optional[Dict[str, Any]]:
    """Run a CLI helper, return parsed JSON or None on any failure."""
    if not shutil.which(cmd[0]) and not cmd[0].startswith("/"):
        return None
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s)
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None
    if proc.returncode != 0:
        return None
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return None


def _ingest(items: List[Dict[str, Any]], builder: Callable[[Dict[str, Any]], AbsorbRecord]) -> int:
    """Apply ``builder`` to every item, write each record, return kept count."""
    kept = 0
    for raw in items:
        if write_record(builder(raw)):
            kept += 1
    return kept


def _email_record(msg: Dict[str, Any]) -> AbsorbRecord:
    return AbsorbRecord(
        record_id=make_id("gog-gmail", str(msg.get("id"))),
        source="gog-gmail",
        captured_utc=utc_now(),
        occurred_utc=msg.get("date_utc") or utc_now(),
        text=f"{msg.get('subject', '')}\n{msg.get('snippet', '')}",
        actors=coalesce_actors(msg.get("from"), *msg.get("to", [])),
        refs=[msg.get("permalink", "")] if msg.get("permalink") else [],
        extra={"thread_id": msg.get("thread_id")},
    )


def _drive_record(f: Dict[str, Any]) -> AbsorbRecord:
    return AbsorbRecord(
        record_id=make_id("gog-drive", str(f.get("id"))),
        source="gog-drive",
        captured_utc=utc_now(),
        occurred_utc=f.get("modified_utc") or utc_now(),
        text=f"Drive: {f.get('name', '')} ({f.get('mime_type', '?')})",
        actors=coalesce_actors(f.get("owner"), *f.get("editors", [])),
        refs=[f.get("web_link", "")] if f.get("web_link") else [],
        extra={"size_bytes": f.get("size_bytes")},
    )


def _calendar_record(ev: Dict[str, Any]) -> AbsorbRecord:
    return AbsorbRecord(
        record_id=make_id("gog-calendar", str(ev.get("id"))),
        source="gog-calendar",
        captured_utc=utc_now(),
        occurred_utc=ev.get("start_utc") or ev.get("created_utc") or utc_now(),
        text=f"{ev.get('summary', '')}\n{ev.get('description', '')}",
        actors=coalesce_actors(*[a.get("email") for a in ev.get("attendees", [])]),
        refs=[ev.get("html_link", "")] if ev.get("html_link") else [],
        extra={"location": ev.get("location"), "duration_minutes": ev.get("duration_minutes")},
    )


def absorb_gmail(user_email: str, hours_back: int = 24) -> int:
    """Absorb recent inbox messages. Returns count kept after relevance filter."""
    payload = _run([
        "python3", "-m", "lib.gog_gmail", "list",
        "--user", user_email, "--hours-back", str(hours_back), "--format", "json",
    ])
    return _ingest(payload.get("messages", []), _email_record) if payload else 0


def absorb_calendar(user_email: str, days_ahead: int = 1) -> int:
    """Absorb upcoming calendar events. Returns count kept."""
    payload = _run([
        "python3", "-m", "lib.gcal", "events",
        "--user", user_email, "--days-ahead", str(days_ahead), "--format", "json",
    ])
    return _ingest(payload.get("events", []), _calendar_record) if payload else 0


def absorb_drive(user_email: str, hours_back: int = 24) -> int:
    """Absorb recent Drive activity. Returns count kept."""
    payload = _run([
        "python3", "-m", "lib.gog_drive", "recent",
        "--user", user_email, "--hours-back", str(hours_back), "--format", "json",
    ])
    return _ingest(payload.get("files", []), _drive_record) if payload else 0


def absorb_all(user_email: str, *, hours_back: int = 24, days_ahead: int = 1) -> Dict[str, int]:
    """Run the full gog sweep and return per-source kept counts."""
    return {
        "gmail": absorb_gmail(user_email, hours_back),
        "calendar": absorb_calendar(user_email, days_ahead),
        "drive": absorb_drive(user_email, hours_back),
    }
