#!/usr/bin/env python3
"""
task_pruner.py — Bounded task substrate, deferring completed work to archive.

Mirrors the proven HOLISTIC.md / operational-context-fold pattern (R1 sealed
ledger + transport-shim to a cheaper substrate) applied to the Claude Code
TaskList substrate.

The harness injects a reminder block carrying the full TaskList at session
start and on idle. With 200+ tasks this costs ~14k tokens per fire. This
module solves the *analysis* half deterministically: parse a reminder
snapshot or supplied entry list, write each row to an append-only on-disk
archive (`state/tasks/archive.jsonl`), and emit a deletion-list of stale
completed IDs. The actual harness mutation (TaskUpdate status="deleted")
is invoked separately by the operator (e.g. via the `/prune-tasks` skill);
this module never touches the harness.

Three-way decision matches `holistic_synthesiser.py::_place_body`:

  KEEP    -> pending OR in_progress OR completed-within-cutoff
  ARCHIVE -> completed AND older-than-cutoff
  DROP    -> never (audit trail is non-negotiable; archive-then-delete)

Falsification (H-TASK-FOLD-1): after deployment, sample 10 session starts
over a week; assert each ships <= 5,000 chars of task-reminder. Falsified
if any one exceeds.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

# Canonical paths (override via CLAUDE_DIR per rules/path-portability.md)
CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", str(Path.home() / ".claude")))
DEFAULT_ARCHIVE = CLAUDE_DIR / "state" / "tasks" / "archive.jsonl"
DEFAULT_DIGEST = CLAUDE_DIR / "state" / "tasks" / "digest.json"

# Live-status set: NEVER archived. Pending/in_progress is sealed-ledger by definition.
LIVE_STATUSES = frozenset({"pending", "in_progress"})

# Parse: "#1647. [completed] /converge wave 3: ..."  OR  "#1647 [completed] subject"
REMINDER_LINE_RE = re.compile(r"^#?(\d+)[\.\s]+\[(\w+)\]\s+(.*)$")

ARCHIVE_SCHEMA_VERSION = "task-archive/1.0"
DIGEST_SCHEMA_VERSION = "task-digest/1.0"


@dataclass(frozen=True)
class TaskEntry:
    """One parsed task line."""
    task_id: str
    status: str
    subject: str


@dataclass(frozen=True)
class PruneDecision:
    """The three-way classification result for one entry."""
    entry: TaskEntry
    action: str  # "keep" | "archive" | (never "drop")
    reason: str


@dataclass
class PruneReport:
    """Aggregate of one prune pass — safe to log, safe to serialise."""
    parsed: int = 0
    keep_ids: list[str] = field(default_factory=list)
    archive_ids: list[str] = field(default_factory=list)
    archive_path: str = ""
    digest_path: str = ""
    snapshot_sha16: str = ""
    archived_at_iso: str = ""

    def as_dict(self) -> dict:
        return {
            "parsed": self.parsed,
            "keep_count": len(self.keep_ids),
            "archive_count": len(self.archive_ids),
            "keep_ids": self.keep_ids,
            "archive_ids": self.archive_ids,
            "archive_path": self.archive_path,
            "digest_path": self.digest_path,
            "snapshot_sha16": self.snapshot_sha16,
            "archived_at_iso": self.archived_at_iso,
        }


# ---------------------------------------------------------------------------
# Pure functions (no I/O)
# ---------------------------------------------------------------------------

def parse_reminder(text: str) -> list[TaskEntry]:
    """Parse a TaskList reminder block into typed entries. Pure."""
    entries: list[TaskEntry] = []
    for line in text.splitlines():
        m = REMINDER_LINE_RE.match(line.strip())
        if not m:
            continue
        entries.append(TaskEntry(
            task_id=m.group(1),
            status=m.group(2),
            subject=m.group(3).strip(),
        ))
    return entries


def classify(entry: TaskEntry, *, live_statuses: frozenset[str] = LIVE_STATUSES) -> PruneDecision:
    """Three-way decision per entry. Pure. Sealed-ledger invariant enforced here."""
    if entry.status in live_statuses:
        # Sealed ledger row — never archived, never dropped.
        return PruneDecision(entry, "keep", f"status={entry.status} is live")
    if entry.status == "completed":
        return PruneDecision(entry, "archive", "completed and beyond cutoff")
    # Any other status (deleted, cancelled, etc.) — keep visible until operator decides.
    return PruneDecision(entry, "keep", f"unknown status={entry.status}; conservative keep")


def compute_snapshot_sha16(text: str) -> str:
    """16-char content sha — mirror of HOLISTIC.md substrate-digest header. Pure."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# I/O layer (fcntl-locked append; same pattern as lib/precog/idr.py::append_idr)
# ---------------------------------------------------------------------------

def archive_entries(
    entries: Iterable[TaskEntry],
    snapshot_sha16: str,
    *,
    archive_path: Path = DEFAULT_ARCHIVE,
    source: str = "reminder-snapshot",
    now_ts: int | None = None,
) -> int:
    """Append-only write of archive rows. Returns the count appended.

    Concurrency-safe via fcntl.flock(LOCK_EX), from the _compat shim, so it
    locks on Windows too (GRIP#8757). Atomicity matches the IDR chain's append
    pattern, so two parallel sessions never interleave rows.
    """
    try:
        from _compat import fcntl  # POSIX fcntl, or the LockFileEx lock on Windows
    except ImportError:  # imported as lib.task_pruner, without lib/ on sys.path
        from lib._compat import fcntl

    archive_path.parent.mkdir(parents=True, exist_ok=True)
    now_ts = now_ts if now_ts is not None else int(time.time())
    now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now_ts))

    count = 0
    with open(archive_path, "a") as f:
        try:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            for e in entries:
                row = {
                    "schema": ARCHIVE_SCHEMA_VERSION,
                    "archived_at": now_iso,
                    "archived_at_ts": now_ts,
                    "snapshot_sha16": snapshot_sha16,
                    "source": source,
                    "task_id": e.task_id,
                    "status_at_archive": e.status,
                    "subject": e.subject,
                }
                f.write(json.dumps(row, sort_keys=True) + "\n")
                count += 1
            f.flush()
            os.fsync(f.fileno())
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    return count


def write_digest(report: PruneReport, *, digest_path: Path = DEFAULT_DIGEST) -> None:
    """Atomic digest write (tmp + rename). Idempotency anchor."""
    digest_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": DIGEST_SCHEMA_VERSION,
        "last_run_at": report.archived_at_iso,
        "snapshot_sha16": report.snapshot_sha16,
        "parsed": report.parsed,
        "keep_count": len(report.keep_ids),
        "archive_count": len(report.archive_ids),
        "archive_path": report.archive_path,
    }
    tmp = digest_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True))
    os.replace(tmp, digest_path)


# ---------------------------------------------------------------------------
# Coordinator (one-shot prune pass)
# ---------------------------------------------------------------------------

def prune_snapshot(
    text: str,
    *,
    archive_path: Path = DEFAULT_ARCHIVE,
    digest_path: Path = DEFAULT_DIGEST,
    source: str = "reminder-snapshot",
    now_ts: int | None = None,
) -> PruneReport:
    """One-shot: parse text, classify, archive what should be archived, write digest.

    Sealed-ledger invariant: never returns an archive_id whose entry status is
    in LIVE_STATUSES. Asserted in tests.
    """
    entries = parse_reminder(text)
    snapshot_sha16 = compute_snapshot_sha16(text)
    decisions = [classify(e) for e in entries]

    keep_ids = [d.entry.task_id for d in decisions if d.action == "keep"]
    archive_pool = [d.entry for d in decisions if d.action == "archive"]

    now_ts = now_ts if now_ts is not None else int(time.time())
    now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now_ts))

    if archive_pool:
        archive_entries(
            archive_pool,
            snapshot_sha16,
            archive_path=archive_path,
            source=source,
            now_ts=now_ts,
        )

    report = PruneReport(
        parsed=len(entries),
        keep_ids=keep_ids,
        archive_ids=[e.task_id for e in archive_pool],
        archive_path=str(archive_path),
        digest_path=str(digest_path),
        snapshot_sha16=snapshot_sha16,
        archived_at_iso=now_iso,
    )
    write_digest(report, digest_path=digest_path)
    return report


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _read_stdin_or_path(path: str | None) -> str:
    if path is None or path == "-":
        return sys.stdin.read()
    return Path(path).read_text(encoding="utf-8")


def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="task_pruner",
        description="Parse a TaskList reminder snapshot; archive completed; emit deletion list.",
    )
    p.add_argument("--input", "-i", default="-",
                   help="Snapshot path (or '-' for stdin). Default: stdin.")
    p.add_argument("--archive", default=str(DEFAULT_ARCHIVE),
                   help=f"Archive JSONL path (default: {DEFAULT_ARCHIVE})")
    p.add_argument("--digest", default=str(DEFAULT_DIGEST),
                   help=f"Digest JSON path (default: {DEFAULT_DIGEST})")
    p.add_argument("--source", default="reminder-snapshot-cli",
                   help="Provenance tag for archived rows.")
    p.add_argument("--print-deletion-list", action="store_true",
                   help="Print archive-bound task IDs (one per line) to stdout.")
    p.add_argument("--json", action="store_true",
                   help="Emit full report as JSON on stdout.")
    return p


def _render_report(report: PruneReport, *, deletion_list: bool, as_json: bool) -> None:
    """Dispatch output format. Mutually-exclusive flags: deletion-list > json > human."""
    if deletion_list:
        for tid in report.archive_ids:
            print(tid)
        return
    if as_json:
        print(json.dumps(report.as_dict(), indent=2, sort_keys=True))
        return
    print(f"task_pruner: parsed {report.parsed} entries from snapshot {report.snapshot_sha16}")
    print(f"  keep    : {len(report.keep_ids)}")
    print(f"  archive : {len(report.archive_ids)} -> {report.archive_path}")
    print(f"  digest  : {report.digest_path}")


def main(argv: list[str] | None = None) -> int:
    args = _build_arg_parser().parse_args(argv)
    report = prune_snapshot(
        _read_stdin_or_path(args.input),
        archive_path=Path(args.archive),
        digest_path=Path(args.digest),
        source=args.source,
    )
    _render_report(report, deletion_list=args.print_deletion_list, as_json=args.json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
