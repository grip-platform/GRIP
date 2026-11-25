"""Cross-session steering inbox (W3 W1 / #3023).

Durable JSONL inbox that concurrent running instances write to; the next
session reads at start. Single-line JSONL appends are used for practical
local-filesystem safety; network filesystems do not provide the same guarantees.

Contract:
- ``inbox_send()`` — any running instance appends a steering entry.
- ``inbox_collect()`` — session start drains all entries (read-once).
- ``inbox_peek()`` — read without draining (diagnostics/status).

High-priority entries are returned first by ``inbox_collect()`` and
``inbox_peek()`` so callers can process urgent steering before normal notes.
"""

import json
import os
import threading
import warnings
from datetime import datetime, timezone
from pathlib import Path

INBOX_FILE = Path.home() / ".claude" / "state" / "steering-inbox.jsonl"
SCHEMA_VERSION = 1
VALID_PRIORITIES = {"normal", "high"}


def _sort_by_priority(entries: list[dict]) -> list[dict]:
    return sorted(entries, key=lambda entry: entry.get("priority") != "high")


def _lost_snapshot_path(snapshot_path: Path) -> Path:
    return snapshot_path.with_name(f"{snapshot_path.name}.lost")


def _recover_lost_snapshots() -> None:
    """Retry snapshots that a previous failed restore made recoverable."""
    pattern = f"{INBOX_FILE.name}.*.drain.lost"
    for snapshot_path in sorted(INBOX_FILE.parent.glob(pattern)):
        _restore_snapshot(snapshot_path)


def _restore_snapshot(snapshot_path: Path) -> None:
    """Put a drain snapshot back if collection fails before it is consumed."""
    if not snapshot_path.exists():
        return

    if not INBOX_FILE.exists():
        os.replace(snapshot_path, INBOX_FILE)
        return

    try:
        with open(snapshot_path, "rb") as snapshot:
            snapshot_bytes = snapshot.read()

        live_needs_separator = INBOX_FILE.stat().st_size > 0
        if live_needs_separator:
            with open(INBOX_FILE, "rb") as inbox:
                inbox.seek(-1, os.SEEK_END)
                live_needs_separator = inbox.read(1) != b"\n"

        # Do not replace a live inbox here: another session may have appended
        # after this restore began. Append the snapshot back instead.
        with open(INBOX_FILE, "ab") as inbox:
            if live_needs_separator and snapshot_bytes:
                inbox.write(b"\n")
            inbox.write(snapshot_bytes)
            if snapshot_bytes and not snapshot_bytes.endswith(b"\n"):
                inbox.write(b"\n")
            inbox.flush()
            os.fsync(inbox.fileno())
    except OSError as exc:
        lost_path = _lost_snapshot_path(snapshot_path)
        try:
            os.replace(snapshot_path, lost_path)
        except OSError as lost_exc:
            warnings.warn(
                "Could not mark steering inbox snapshot recoverable: "
                f"{lost_exc}; original restore error: {exc}",
                RuntimeWarning,
            )
            raise
        warnings.warn(
            f"Could not restore steering inbox snapshot; saved for retry at {lost_path}: {exc}",
            RuntimeWarning,
        )
        return
    snapshot_path.unlink(missing_ok=True)


def inbox_send(
    description: str,
    source: str = "unknown",
    instance_id: str | None = None,
    priority: str = "normal",
) -> dict:
    """Append one steering-inbox entry.

    Args:
        description: Human-readable note for the next session.
        source: Which skill / agent / module produced it.
        instance_id: Optional session instance id for provenance.
        priority: ``"normal"`` or ``"high"`` (checked first at session start).
    Returns:
        The entry dict written.
    """
    if not description or not description.strip():
        raise ValueError("description must be a non-empty string")
    if priority not in VALID_PRIORITIES:
        raise ValueError(f"priority must be one of: {', '.join(sorted(VALID_PRIORITIES))}")

    INBOX_FILE.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "v": SCHEMA_VERSION,
        "ts": datetime.now(timezone.utc).isoformat(),
        "description": description,
        "source": source,
        "instance_id": instance_id,
        "priority": priority,
    }
    with open(INBOX_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, default=str) + "\n")
        f.flush()
        os.fsync(f.fileno())
    return entry


def inbox_collect() -> list[dict]:
    """Read and drain the steering inbox. Returns all entries.

    Call at session start to surface injections from other instances.
    The current file is atomically renamed to a snapshot before reading, so
    writers that append during collection create a fresh inbox file.
    """
    _recover_lost_snapshots()

    if not INBOX_FILE.exists():
        return []
    snapshot_path = INBOX_FILE.with_suffix(
        f".jsonl.{os.getpid()}.{threading.get_ident()}.drain"
    )
    try:
        os.replace(INBOX_FILE, snapshot_path)
    except FileNotFoundError:
        return []

    entries: list[dict] = []
    try:
        with open(snapshot_path, encoding="utf-8") as f:
            for line in f:
                stripped = line.strip()
                if stripped:
                    try:
                        entries.append(json.loads(stripped))
                    except json.JSONDecodeError:
                        warnings.warn("Skipped malformed steering inbox line", category=RuntimeWarning)
    except Exception:
        _restore_snapshot(snapshot_path)
        raise
    else:
        snapshot_path.unlink(missing_ok=True)
    return _sort_by_priority(entries)


def inbox_peek() -> list[dict]:
    """Read current inbox entries without draining."""
    if not INBOX_FILE.exists():
        return []
    entries: list[dict] = []
    try:
        with open(INBOX_FILE, encoding="utf-8") as f:
            for line in f:
                stripped = line.strip()
                if stripped:
                    try:
                        entries.append(json.loads(stripped))
                    except json.JSONDecodeError:
                        warnings.warn("Skipped malformed steering inbox line", category=RuntimeWarning)
    except OSError as exc:
        warnings.warn(f"Could not read steering inbox: {exc}", category=RuntimeWarning)
        return []
    return _sort_by_priority(entries)
