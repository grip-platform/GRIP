#!/usr/bin/env python3
"""grip-kono-prune — coordinated prune of empty KONO instance shells.

Delete set = instances that are truly orphaned garbage, guarded three ways
(broly-ratified predicate + hardening from the 2026-08-18 prune):
  1. zero attestations       (id NOT IN kono_attestations.instance_id)
  2. no embedding provenance (id NOT IN embeddings.origin_instance_id)
  3. not anyone's parent     (id NOT IN kono_instances.parent_id)
  4. not recently active     (last_active_at NULL or older than --min-age-days)

Dry-run by default; --apply commits. Backup + verification are mandatory with
--apply. FK is OFF in this codebase, so the guards above are the only
protection against orphaning a live session's rows.

The guards are re-applied per row INSIDE the write transaction, not just when
the candidate list is built. In a live multi-agent installation another
connection can attest an instance, attach an embedding, give it a child, or
refresh last_active_at in the window between the two, and deleting the stale id
list would destroy a row that is no longer garbage. A row that stopped being
garbage now simply matches nothing and is reported as skipped.

Verification (no orphaned attestations, no dangling parent references, counts
unchanged) also runs BEFORE the commit, so a failed check rolls back by itself
instead of leaving the operator to restore the backup by hand.

Usage:
    bin/grip-kono-prune.py                 # dry run
    bin/grip-kono-prune.py --apply         # backup, delete, verify
    bin/grip-kono-prune.py --min-age-days 1
"""
import argparse
import sqlite3
import sys
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

DB = Path(__file__).resolve().parent.parent / "embeddings.db"


def _backup_dir() -> Path:
    #: Deliberately outside the repo, so the backup survives the repo
    #: operations it exists to survive. Created on demand: it is gitignored and
    #: nothing else creates it, so on a fresh install it will not exist yet.
    return Path.home() / ".grip" / "backups"


def ro():
    return sqlite3.connect(f"file:{DB}?mode=ro", uri=True)


#: The guard predicate, in one place. The candidate scan and the delete share
#: it verbatim so the two can never drift apart, and so the delete can re-check
#: it atomically instead of trusting an id list collected earlier.
#: Takes one parameter: the recency cutoff.
#:
#: The recency test compares ISO timestamps as TEXT, which assumes every
#: writer stores the same offset format. Measured, the exposure is knife-edge:
#: only a NAIVE value landing on the cutoff second sorts before the
#: offset-bearing cutoff and reads as older; one second later is already safe,
#: because the digit difference decides the comparison before the missing
#: "+00:00" does. Nothing can take that path today — last_active_at is written
#: from an aware datetime (lib/kono_lifecycle.py) and all 85,737 live rows
#: carry "+00:00". test_recency_guard_assumes_a_consistent_offset_format pins
#: the boundary so it fails if a naive writer ever appears.
_GUARDS = """
        id NOT IN (SELECT DISTINCT instance_id FROM kono_attestations WHERE instance_id IS NOT NULL)
    AND id NOT IN (SELECT DISTINCT origin_instance_id FROM embeddings WHERE origin_instance_id IS NOT NULL)
    AND id NOT IN (SELECT parent_id FROM kono_instances WHERE parent_id IS NOT NULL)
    AND (last_active_at IS NULL OR last_active_at < ?)
"""

_SELECT_CANDIDATES = "SELECT id FROM kono_instances WHERE " + _GUARDS

#: Re-applies every guard at delete time. Params: (instance_id, cutoff).
#: A row that stopped being garbage after the scan simply matches nothing and
#: is left alone, so the race cannot destroy a live instance.
_GUARDED_DELETE = "DELETE FROM kono_instances WHERE id = ? AND (" + _GUARDS + ")"


def candidates(c, cutoff: str) -> list:
    return [r[0] for r in c.execute(_SELECT_CANDIDATES, (cutoff,)).fetchall()]


def counts(c):
    return {
        "instances": c.execute("SELECT COUNT(*) FROM kono_instances").fetchone()[0],
        "attestations": c.execute("SELECT COUNT(*) FROM kono_attestations").fetchone()[0],
        "origin_refs": c.execute(
            "SELECT COUNT(*) FROM embeddings WHERE origin_instance_id IS NOT NULL").fetchone()[0],
    }


def orphans(c):
    """Attestations pointing at an instance that does not exist.

    The `a.instance_id IS NOT NULL` filter matters and must match the guard
    predicate above, which already treats NULL as "references nothing". Without
    it the LEFT JOIN finds no match for a NULL row, `i.id IS NULL` is true, and
    a row that never pointed anywhere is reported as corruption. Since
    verification now runs before the commit, that would roll back every --apply
    for good.
    """
    return c.execute(
        "SELECT COUNT(*) FROM kono_attestations a "
        "LEFT JOIN kono_instances i ON a.instance_id = i.id "
        "WHERE a.instance_id IS NOT NULL AND i.id IS NULL"
    ).fetchone()[0]


def dangling_parents(c):
    """Children whose parent_id points at a row that no longer exists.

    The other way a prune can corrupt the table. orphans() only watches
    attestations, so without this a broken parent chain would pass verification
    unnoticed. FK enforcement is off in this codebase, so nothing else catches it.
    """
    return c.execute(
        "SELECT COUNT(*) FROM kono_instances child "
        "LEFT JOIN kono_instances parent ON child.parent_id = parent.id "
        "WHERE child.parent_id IS NOT NULL AND parent.id IS NULL"
    ).fetchone()[0]


def _backup_path() -> Path:
    """Resolved at call time, not import time, so Path.home() is honoured."""
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return _backup_dir() / f"embeddings.db.pre-prune-{ts}"


def write_backup(bak: Path) -> Path:
    """Take a consistent copy of the database, and prove it is readable.

    Not a file copy. embeddings.db runs in WAL mode, so a plain copy of the
    main file alone leaves behind everything committed to embeddings.db-wal
    but not yet checkpointed, and can catch a page mid-write while another
    process is writing. The result would be a backup missing the newest data,
    or a corrupt one — discovered only when someone needs it.

    sqlite3's own backup API handles a live WAL database properly. The copy is
    then reopened and integrity-checked, because a backup that cannot be read
    is not a backup, and this one is the only thing standing behind the delete.
    """
    bak.parent.mkdir(parents=True, exist_ok=True)

    # Closed explicitly, not via `with`: sqlite3's context manager commits a
    # transaction, it does NOT close the connection. Closing is what
    # checkpoints the destination's own WAL back into the file, leaving one
    # self-contained backup instead of a main file plus -wal and -shm sidecars
    # that a hand restore would leave behind.
    src = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    try:
        dst = sqlite3.connect(str(bak), isolation_level=None)
        try:
            src.backup(dst)
            # The backup inherits WAL mode from the source, which would leave
            # the archive as three files (.db, -wal, -shm) that must travel
            # together. An archive should be one file you can copy back, so
            # fold the journal in.
            dst.execute("PRAGMA journal_mode=DELETE")
        finally:
            dst.close()
    finally:
        src.close()

    chk = sqlite3.connect(str(bak))
    try:
        result = chk.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        chk.close()
    if result != "ok":
        raise RuntimeError(f"backup failed integrity_check ({result}): {bak}")
    return bak


def _verify(c, before: dict, after: dict, deleted: int) -> list:
    """Reasons the prune is unsafe to commit. Empty list means safe."""
    reasons = []
    # The width of the delete. Nothing else here notices extra INSTANCE rows
    # disappearing: a DELETE trigger can remove bystanders (triggers fire
    # whether or not foreign keys are on), and such a delete leaves no orphan
    # and no dangling parent, so every other check below passes it. This ties
    # the outcome to the number of rows the tool actually meant to remove.
    expected = before["instances"] - deleted
    if after["instances"] != expected:
        reasons.append(
            f"instance count is {after['instances']}, expected {expected} "
            f"({before['instances']} - {deleted} deleted): the delete was wider "
            "than the candidate list authorised")
    orphaned = orphans(c)
    if orphaned:
        reasons.append(f"{orphaned} orphaned attestations")
    dangling = dangling_parents(c)
    if dangling:
        reasons.append(f"{dangling} dangling parent references")
    if after["attestations"] != before["attestations"]:
        reasons.append("attestation count changed")
    if after["origin_refs"] != before["origin_refs"]:
        reasons.append("embedding provenance count changed")
    return reasons


def prune_atomically(ids: list, cutoff: str) -> tuple:
    """Delete the guarded set inside ONE write transaction.

    Returns (deleted, skipped, reasons, before, after). ``reasons`` non-empty
    means the transaction was rolled back and nothing was deleted.

    ``before`` and ``after`` are measured INSIDE the transaction and returned
    rather than re-read afterwards. Read on a separate connection, they could
    disagree with the delete they describe the moment another agent commits in
    the window, leaving a paper trail that contradicts its own result.

    Every guard is re-applied per row at delete time, and verification runs
    BEFORE the commit, so a failed check rolls back automatically rather than
    leaving the operator to restore a backup by hand.
    """
    cw = sqlite3.connect(str(DB), isolation_level=None)
    try:
        cw.execute("BEGIN IMMEDIATE")  # take the write lock up front
        before = counts(cw)
        deleted = 0
        for i in ids:
            deleted += cw.execute(_GUARDED_DELETE, (i, cutoff)).rowcount
        after = counts(cw)
        reasons = _verify(cw, before, after, deleted)
        cw.execute("ROLLBACK" if reasons else "COMMIT")
        return deleted, len(ids) - deleted, reasons, before, after
    except Exception:
        # Only roll back a transaction that exists. If BEGIN IMMEDIATE itself
        # failed — another agent holding the write lock, which is the very
        # contention this tool guards against — there is nothing to roll back,
        # and an unconditional ROLLBACK raises "cannot rollback - no
        # transaction is active", replacing the lock error the operator needs.
        if cw.in_transaction:
            cw.execute("ROLLBACK")
        raise
    finally:
        cw.close()


def _parse_args():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="commit the delete (default: dry run)")
    ap.add_argument("--min-age-days", type=int, default=7,
                    help="keep rows active within this many days (default 7)")
    args = ap.parse_args()

    # A negative age puts the cutoff in the FUTURE, where every row — including
    # a session active seconds ago — satisfies "last_active_at < cutoff". The
    # candidate scan and the delete share that cutoff, so the width check would
    # dutifully authorise the over-broad set and the run would print a green
    # VERIFIED. Foreign keys are off, so on the most destructive command in the
    # repo a stray minus sign is the difference between a prune and an outage.
    if args.min_age_days < 0:
        ap.error(f"--min-age-days must not be negative (got {args.min_age_days}): "
                 "a negative age moves the cutoff into the future and would "
                 "select every instance, including live ones")
    return args


def _report(deleted: int, skipped: int, before: dict, after: dict) -> None:
    print(f"deleted {deleted} instances")
    if skipped:
        print(f"skipped {skipped} that stopped being garbage after the scan "
              "(attested, referenced, given a child, or re-activated)")
    # Both measured inside the write transaction, so they describe the delete
    # reported above rather than whatever the database looks like by now.
    print(f"before: {before}")
    print(f"after : {after}")
    print("VERIFIED: instance count matches the delete, no orphans, "
          "no dangling parents, attestations/refs unchanged.")


def main() -> int:
    args = _parse_args()

    cutoff = (datetime.now(timezone.utc) - timedelta(days=args.min_age_days)).isoformat()
    # closing(): ro() opens a fresh connection each call, and an unclosed one
    # keeps its WAL sidecars alive for the life of the process — which matters
    # in the long-running multi-agent installs this tool is aimed at.
    with closing(ro()) as c:
        ids = candidates(c, cutoff)
    print(f"delete set: {len(ids)} instances "
          f"(0 attestations, 0 origin refs, not a parent, inactive >{args.min_age_days}d)")

    if not args.apply:
        print("DRY RUN — no changes. re-run with --apply.")
        print("sample:", ids[:5])
        return 0

    bak = write_backup(_backup_path())
    print("backup ->", bak)

    deleted, skipped, reasons, before, after = prune_atomically(ids, cutoff)

    if reasons:
        print("VERIFY FAILED — rolled back, nothing deleted: "
              + "; ".join(reasons), file=sys.stderr)
        print("backup kept at:", bak, file=sys.stderr)
        return 1

    _report(deleted, skipped, before, after)
    return 0


if __name__ == "__main__":
    sys.exit(main())
