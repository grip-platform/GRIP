#!/usr/bin/env python3
"""Session Archiver — compress and archive old GRIP session data.

Discovers JSONL session files in projects/*/ directories, compresses
sessions older than N days, maintains an archive index, and supports
restore-by-ID.

Usage:
    python3 lib/session_archiver.py              # archive sessions > 7 days old
    python3 lib/session_archiver.py --days 30    # custom age threshold
    python3 lib/session_archiver.py --dry-run    # preview without changes
    python3 lib/session_archiver.py --restore <session-id>
    python3 lib/session_archiver.py --list       # list archived sessions
    python3 lib/session_archiver.py --json       # JSON output
    python3 lib/session_archiver.py --min-size 1M
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import re
import shutil
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class SessionInfo:
    """Metadata about a discovered session file."""

    session_id: str
    path: Path
    size: int  # bytes
    mtime: float  # epoch
    message_count: int = 0
    first_timestamp: Optional[str] = None
    last_timestamp: Optional[str] = None


@dataclass
class ArchiveEntry:
    """A single entry in the archive index."""

    session_id: str
    original_path: str
    archived_at: str
    original_size: int
    compressed_size: int
    message_count: int
    date_range: dict = field(default_factory=dict)  # {first, last}


@dataclass
class ArchiveResult:
    """Result of an archive operation."""

    archived: list[ArchiveEntry] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    total_original: int = 0
    total_compressed: int = 0

    @property
    def space_saved(self) -> int:
        return self.total_original - self.total_compressed


# ---------------------------------------------------------------------------
# Size parsing
# ---------------------------------------------------------------------------

_SIZE_UNITS = {"B": 1, "K": 1024, "M": 1024**2, "G": 1024**3}


def parse_size(value: str) -> int:
    """Parse a human-readable size string like '1M', '500K', '2G'.

    Accepts bare integers (bytes) or integers/floats followed by a unit
    suffix (B, K, M, G — case-insensitive).
    """
    value = value.strip()
    if not value:
        raise ValueError("Empty size string")

    match = re.fullmatch(r"(\d+(?:\.\d+)?)\s*([BKMG])?", value, re.IGNORECASE)
    if not match:
        raise ValueError(f"Invalid size format: {value!r}")

    number = float(match.group(1))
    unit = (match.group(2) or "B").upper()
    return int(number * _SIZE_UNITS[unit])


def format_size(nbytes: int) -> str:
    """Format bytes into a human-readable string."""
    if nbytes < 1024:
        return f"{nbytes} B"
    for unit in ("K", "M", "G"):
        nbytes /= 1024
        if nbytes < 1024:
            return f"{nbytes:.1f} {unit}"
    return f"{nbytes:.1f} T"


# ---------------------------------------------------------------------------
# Session discovery
# ---------------------------------------------------------------------------

def discover_sessions(base_dir: Path) -> list[SessionInfo]:
    """Find all JSONL session files under projects/*/ directories."""
    sessions: list[SessionInfo] = []
    projects_dir = base_dir / "projects"
    if not projects_dir.is_dir():
        return sessions

    for project_dir in sorted(projects_dir.iterdir()):
        if not project_dir.is_dir():
            continue
        for jsonl_file in sorted(project_dir.glob("*.jsonl")):
            session_id = jsonl_file.stem
            stat = jsonl_file.stat()
            info = SessionInfo(
                session_id=session_id,
                path=jsonl_file,
                size=stat.st_size,
                mtime=stat.st_mtime,
            )
            _extract_session_metadata(info)
            sessions.append(info)

    return sessions


def _extract_session_metadata(info: SessionInfo) -> None:
    """Read JSONL file to count messages and extract date range."""
    timestamps: list[str] = []
    count = 0

    try:
        with open(info.path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                count += 1
                try:
                    obj = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                # Look for timestamp fields
                ts = obj.get("timestamp") or obj.get("archived_at")
                if ts and isinstance(ts, str):
                    timestamps.append(ts)
                # Nested snapshot timestamp
                snap = obj.get("snapshot", {})
                if isinstance(snap, dict):
                    snap_ts = snap.get("timestamp")
                    if snap_ts and isinstance(snap_ts, str):
                        timestamps.append(snap_ts)
    except (OSError, IOError):
        pass

    info.message_count = count
    if timestamps:
        timestamps.sort()
        info.first_timestamp = timestamps[0]
        info.last_timestamp = timestamps[-1]


# ---------------------------------------------------------------------------
# Age / size / pattern filtering
# ---------------------------------------------------------------------------

def filter_by_age(sessions: list[SessionInfo], max_age_days: int,
                  now: Optional[float] = None) -> list[SessionInfo]:
    """Return sessions whose mtime is older than max_age_days."""
    now = now or time.time()
    cutoff = now - (max_age_days * 86400)
    return [s for s in sessions if s.mtime < cutoff]


def filter_by_size(sessions: list[SessionInfo], min_bytes: int) -> list[SessionInfo]:
    """Return sessions whose file size >= min_bytes."""
    return [s for s in sessions if s.size >= min_bytes]


def filter_by_pattern(sessions: list[SessionInfo], pattern: str) -> list[SessionInfo]:
    """Return sessions whose ID or path matches the given regex pattern."""
    regex = re.compile(pattern)
    return [s for s in sessions if regex.search(s.session_id) or regex.search(str(s.path))]


# ---------------------------------------------------------------------------
# Archive index management
# ---------------------------------------------------------------------------

def _index_path(archive_dir: Path) -> Path:
    return archive_dir / "index.json"


def load_index(archive_dir: Path) -> list[ArchiveEntry]:
    """Load the archive index, returning an empty list if absent."""
    idx_path = _index_path(archive_dir)
    if not idx_path.exists():
        return []
    try:
        with open(idx_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return [ArchiveEntry(**entry) for entry in data]
    except (json.JSONDecodeError, TypeError, KeyError):
        return []


def save_index(archive_dir: Path, entries: list[ArchiveEntry]) -> None:
    """Persist the archive index."""
    idx_path = _index_path(archive_dir)
    with open(idx_path, "w", encoding="utf-8") as f:
        json.dump([asdict(e) for e in entries], f, indent=2)


def get_archived_ids(archive_dir: Path) -> set[str]:
    """Return the set of session IDs already in the archive."""
    return {e.session_id for e in load_index(archive_dir)}


# ---------------------------------------------------------------------------
# Core operations: archive, restore, list
# ---------------------------------------------------------------------------

def archive_sessions(
    sessions: list[SessionInfo],
    archive_dir: Path,
    dry_run: bool = False,
) -> ArchiveResult:
    """Compress and archive the given sessions."""
    result = ArchiveResult()
    already_archived = get_archived_ids(archive_dir)
    index = load_index(archive_dir)

    if not dry_run:
        archive_dir.mkdir(parents=True, exist_ok=True)

    for session in sessions:
        if session.session_id in already_archived:
            result.skipped.append(session.session_id)
            continue

        dest = archive_dir / f"{session.session_id}.jsonl.gz"
        entry = ArchiveEntry(
            session_id=session.session_id,
            original_path=str(session.path),
            archived_at=datetime.now(timezone.utc).isoformat(),
            original_size=session.size,
            compressed_size=0,
            message_count=session.message_count,
            date_range={
                "first": session.first_timestamp or "",
                "last": session.last_timestamp or "",
            },
        )

        if dry_run:
            # Estimate compression (typically 85-95% for JSON text)
            entry.compressed_size = max(1, session.size // 8)
            result.archived.append(entry)
            result.total_original += session.size
            result.total_compressed += entry.compressed_size
            continue

        try:
            # Compress
            with open(session.path, "rb") as f_in:
                with gzip.open(dest, "wb", compresslevel=9) as f_out:
                    shutil.copyfileobj(f_in, f_out)

            entry.compressed_size = dest.stat().st_size
            result.total_original += session.size
            result.total_compressed += entry.compressed_size

            # Remove original
            os.unlink(session.path)

            # Update index
            index.append(entry)
            result.archived.append(entry)
            already_archived.add(session.session_id)

        except (OSError, IOError) as exc:
            result.errors.append(f"{session.session_id}: {exc}")
            # Clean up partial archive
            if dest.exists():
                try:
                    os.unlink(dest)
                except OSError:
                    pass

    if not dry_run and result.archived:
        save_index(archive_dir, index)

    return result


def restore_session(session_id: str, archive_dir: Path) -> Optional[str]:
    """Restore a session from the archive. Returns the restored path or None."""
    index = load_index(archive_dir)
    entry = None
    for e in index:
        if e.session_id == session_id:
            entry = e
            break

    if entry is None:
        return None

    gz_path = archive_dir / f"{session_id}.jsonl.gz"
    if not gz_path.exists():
        return None

    dest = Path(entry.original_path)
    dest.parent.mkdir(parents=True, exist_ok=True)

    # Decompress
    with gzip.open(gz_path, "rb") as f_in:
        with open(dest, "wb") as f_out:
            shutil.copyfileobj(f_in, f_out)

    # Remove from archive
    os.unlink(gz_path)

    # Update index
    index = [e for e in index if e.session_id != session_id]
    save_index(archive_dir, index)

    return str(dest)


def list_archived(archive_dir: Path) -> list[ArchiveEntry]:
    """Return all entries in the archive index."""
    return load_index(archive_dir)


def size_report(archive_dir: Path) -> dict:
    """Compute disk space statistics for the archive."""
    index = load_index(archive_dir)
    total_original = sum(e.original_size for e in index)
    total_compressed = sum(e.compressed_size for e in index)
    return {
        "session_count": len(index),
        "total_original": total_original,
        "total_compressed": total_compressed,
        "space_saved": total_original - total_compressed,
        "compression_ratio": (
            round(total_compressed / total_original, 4) if total_original > 0 else 0
        ),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Session Archiver — compress and archive old GRIP session data"
    )
    parser.add_argument(
        "--base-dir",
        type=Path,
        default=Path(os.environ.get("CLAUDE_DIR", Path.home() / ".claude")),
        help="Base directory containing projects/ (default: ~/.claude)",
    )
    parser.add_argument(
        "--archive-dir",
        type=Path,
        default=None,
        help="Archive output directory (default: <base-dir>/archive/sessions)",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=7,
        help="Archive sessions older than N days (default: 7)",
    )
    parser.add_argument(
        "--min-size",
        type=str,
        default=None,
        help="Only archive sessions larger than this size (e.g. 1M, 500K)",
    )
    parser.add_argument(
        "--pattern",
        type=str,
        default=None,
        help="Only archive sessions matching this regex pattern",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview what would be archived without making changes",
    )
    parser.add_argument(
        "--restore",
        type=str,
        metavar="SESSION_ID",
        default=None,
        help="Restore a session from the archive by ID",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        dest="list_archived",
        help="List all archived sessions",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Output in JSON format",
    )
    parser.add_argument(
        "--report",
        action="store_true",
        help="Show disk space savings report",
    )
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    base_dir = args.base_dir.resolve()
    archive_dir = (
        args.archive_dir.resolve()
        if args.archive_dir
        else base_dir / "archive" / "sessions"
    )

    # --- Restore mode ---
    if args.restore:
        restored = restore_session(args.restore, archive_dir)
        if restored:
            if args.json_output:
                print(json.dumps({"restored": args.restore, "path": restored}))
            else:
                print(f"Restored {args.restore} -> {restored}")
            return 0
        else:
            msg = f"Session {args.restore} not found in archive"
            if args.json_output:
                print(json.dumps({"error": msg}))
            else:
                print(msg, file=sys.stderr)
            return 1

    # --- List mode ---
    if args.list_archived:
        entries = list_archived(archive_dir)
        if args.json_output:
            print(json.dumps([asdict(e) for e in entries], indent=2))
        else:
            if not entries:
                print("No archived sessions.")
            else:
                print(f"{'Session ID':<40} {'Original':>10} {'Compressed':>10} {'Messages':>8} {'Archived At'}")
                print("-" * 110)
                for e in entries:
                    print(
                        f"{e.session_id:<40} "
                        f"{format_size(e.original_size):>10} "
                        f"{format_size(e.compressed_size):>10} "
                        f"{e.message_count:>8} "
                        f"{e.archived_at[:19]}"
                    )
        return 0

    # --- Report mode ---
    if args.report:
        report = size_report(archive_dir)
        if args.json_output:
            print(json.dumps(report, indent=2))
        else:
            print(f"Archived sessions: {report['session_count']}")
            print(f"Original total:    {format_size(report['total_original'])}")
            print(f"Compressed total:  {format_size(report['total_compressed'])}")
            print(f"Space saved:       {format_size(report['space_saved'])}")
            if report['total_original'] > 0:
                pct = (1 - report['compression_ratio']) * 100
                print(f"Compression:       {pct:.1f}% reduction")
        return 0

    # --- Archive mode (default) ---
    sessions = discover_sessions(base_dir)

    # Apply filters
    sessions = filter_by_age(sessions, args.days)

    if args.min_size:
        min_bytes = parse_size(args.min_size)
        sessions = filter_by_size(sessions, min_bytes)

    if args.pattern:
        sessions = filter_by_pattern(sessions, args.pattern)

    if not sessions:
        if args.json_output:
            print(json.dumps({"archived": [], "message": "No sessions match criteria"}))
        else:
            print("No sessions match the archive criteria.")
        return 0

    result = archive_sessions(sessions, archive_dir, dry_run=args.dry_run)

    if args.json_output:
        output = {
            "dry_run": args.dry_run,
            "archived": [asdict(e) for e in result.archived],
            "skipped": result.skipped,
            "errors": result.errors,
            "total_original": result.total_original,
            "total_compressed": result.total_compressed,
            "space_saved": result.space_saved,
        }
        print(json.dumps(output, indent=2))
    else:
        prefix = "[DRY RUN] " if args.dry_run else ""
        print(f"{prefix}Archived: {len(result.archived)} sessions")
        if result.skipped:
            print(f"{prefix}Skipped (already archived): {len(result.skipped)}")
        if result.errors:
            print(f"{prefix}Errors: {len(result.errors)}")
            for err in result.errors:
                print(f"  - {err}")
        print(f"{prefix}Original:   {format_size(result.total_original)}")
        print(f"{prefix}Compressed: {format_size(result.total_compressed)}")
        print(f"{prefix}Saved:      {format_size(result.space_saved)}")

    return 1 if result.errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
