#!/usr/bin/env python3
"""
mesh_gc.py — garbage-collection + honest-counting substrate for grip-session-mesh.

Two distinct defects this module fixes (both verified live 2026-06-02 against
~/.grip-session-mesh/presence.db; 1339 undelivered inbox rows, 170 session
rows, 47 LIVE.md files vs ~5 truly-live sessions):

  BUG 1 — no garbage-collection / TTL anywhere. A broadcast INSERTs one inbox
  row per then-live recipient; if that recipient dies before its poll-hook
  drains it (hooks/mesh-inbox-poll-hook.py sets delivered=1), the row stays
  delivered=0 forever. Dead session rows and stale ~/.claude/sync/cc-*-LIVE.md
  files accrete the same way. mesh_state then COUNT(*)s these tombstones and
  frames STALE data as CURRENT "waiting work" (the exact V>> complaint).

  BUG 2 — the liveness filter in bin/mesh was a broken STRING comparison.
  Heartbeats are stored ISO-8601 with a 'T' separator + 'Z' suffix
  ('2026-06-02T01:41:41.000Z'); sqlite datetime('now','-1 hour') yields a
  space-separated, suffix-less form ('2026-06-02 00:41:41'). SQLite compares
  them LEXICALLY and 'T'(0x54) > ' '(0x20), so any heartbeat sharing the
  threshold's calendar DATE passes regardless of its real time (a 92-min-stale
  session counted as live), AND a genuinely-recent heartbeat dated to the prior
  calendar day is wrongly excluded. The fix is to coerce BOTH sides:
  datetime(last_heartbeat) > datetime('now', ...). That single coercion is the
  precondition for ANY correct count or GC and is exported here as one constant.

DESIGN INVARIANTS (each folds a survived council objection into a hard rule):

  * REPORTING and DELETION are SEPARATE concerns (gc-safety-adversary's
    strongest objection). The honest COUNT uses the -1h window so mesh_state is
    truthful immediately; the DELETE uses a deliberately LOOSER 24h fail-closed
    floor so an idle-but-alive session (heartbeat fires only on a tool call, so
    a live session can be silent for an operator coffee break) can NEVER have
    its queued messages dropped. Honest reporting solves the framing complaint
    on day one; patient deletion solves the safety requirement.

  * FAIL-CLOSED: an inbox row is collected ONLY if it is BOTH old
    (created_at < now-TTL_MSG) AND its recipient has been silent past TTL_RECIP.
    Under any ambiguity the row is KEPT (under-collect, never drop a live
    message). An undelivered row for a recipient alive within TTL_RECIP is never
    a deletion candidate.

  * STRICT ORDER inbox -> sessions -> LIVE.md. Today 0 undelivered rows are
    orphaned, so a session row's last_heartbeat is load-bearing for judging its
    inbox rows' liveness. Deleting sessions first would destroy that signal and
    orphan the inbox rows. TTL_SESSION >= TTL_RECIP guarantees a recipient is
    never deleted while it could still own sparable inbox rows.

  * CONCURRENCY-SAFE under 6+ live sessions sharing one sqlite DB
    (journal_mode=delete, busy_timeout=0 persisted). Each GC connection sets its
    OWN timeout (never inherits 0); the liveness sub-select and the DELETE run as
    ONE statement in ONE transaction (so a window-edge recipient cannot flip
    live->dead between a SELECT and a DELETE); DELETEs are CHUNKED (LIMIT per
    txn, loop) so each transaction stays well under the busy_timeout and cannot
    starve the live sessions' poll-hook UPDATE delivered=1 into a silent
    SQLITE_BUSY. Indexes are ensured so GC + poll are O(log n), not full scans.

  * MIGRATION + STRUCTURAL fix share ONE predicate. The one-shot drain is just
    `reap()` run once; recurrence is prevented by calling `maybe_reap()`
    opportunistically from the CLI. Both use the identical TTLs/SQL defined here.

  * The reaper ships with its own FALSIFIER (knowledge-maturation-functor): every
    deletion is audit-logged to logs/mesh-gc.jsonl so a row a recipient later
    needed can be detected. Wrong if it ever drops a row a live recipient
    needed (target 0), or never collects the genuine backlog (pure friction).

Stdlib only. DI-testable via db_path / sync_dir / now arguments — no global
state, no network, no clock coupling in the core SQL (sqlite computes 'now').
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

# --- Tunable TTLs (hours). Looser-than-report on purpose; see module docstring.
TTL_MSG_HOURS = 24       # an undelivered row younger than this is never garbage
TTL_RECIP_HOURS = 24     # a recipient silent less than this is treated as maybe-alive
TTL_SESSION_HOURS = 24   # a session row silent less than this is kept (>= TTL_RECIP)
TTL_DELIVERED_HOURS = 24  # delivered=1 rows retained this long for audit, then reaped
TTL_SNAPSHOT_HOURS = 24  # LIVE.md whose backing session is gone + mtime older -> reap

# The report window — honest "live right now" framing. Decoupled from the TTLs
# above ON PURPOSE: report tight (truthful now), delete loose (safe).
REPORT_WINDOW = "-1 hour"

_CHUNK = 500             # max rows per DELETE transaction (bounds the write-lock window)
_CONN_TIMEOUT_S = 5.0    # GC connections MUST set this — never inherit busy_timeout=0

# --- The ONE canonical liveness coercion. Every count and every GC filter that
# asks "is this session alive?" MUST route through this so BUG 2 cannot recur.
# Use as: f"datetime(last_heartbeat) > datetime('now','{window}')".
def live_session_predicate(window: str) -> str:
    """SQL boolean fragment: a session row is live within *window* (e.g. '-1 hour').

    Coerces last_heartbeat through datetime() so the 'T'/'Z' ISO form compares
    correctly against datetime('now', window). This is the fix for BUG 2 and the
    single source of truth shared by reporting and GC.
    """
    return f"datetime(last_heartbeat) > datetime('now', '{window}')"


# --- CONFIRMED liveness — REPORTING ONLY. Never used by the GC delete path.
def confirmed_live_session_predicate(window: str) -> str:
    """SQL boolean: live within *window* AND has actually beaten at least once.

    ``live_session_predicate`` asks only "did the heartbeat land inside the
    window", and a row that NEVER beat still passes it, because registration
    writes ``last_heartbeat = registered_at`` — a registration masquerades as a
    heartbeat for the whole window. Measured 2026-09-10 on the live presence DB:
    11 session rows, 6 real sessions, and 5 short-lived processes spawned from a
    scratchpad directory (all named ``cwd``) that exited without ever beating.
    ``mesh state`` called all 11 "running" — a 45% over-report of reachable
    peers. Requiring a beat separates them: 5/5 ghosts out, 6/6 real kept.

    SCOPE — reporting and discovery ONLY, never deletion. The GC's DELETE path
    keeps the looser ``live_session_predicate`` deliberately: this one is
    STRICTER, and a stricter test on the delete side would reap a real session's
    inbox during the gap between registration and its first heartbeat. Reporting
    and deletion are separate concerns (module docstring).
    """
    return (
        f"{live_session_predicate(window)} "
        "AND datetime(last_heartbeat) > datetime(registered_at)"
    )


def _audit_log_path() -> Path:
    env = os.environ.get("CLAUDE_MESH_GC_LOG")
    if env:
        return Path(env)
    return Path.home() / ".claude" / "logs" / "mesh-gc.jsonl"


def _audit(records: list[dict], log_path: Path | None = None) -> None:
    """Append one JSONL line per deleted artefact (the reaper's own falsifier).

    Best-effort: a logging failure must never abort GC or the caller.
    """
    if not records:
        return
    path = log_path or _audit_log_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            for rec in records:
                fh.write(json.dumps(rec, separators=(",", ":")) + "\n")
    except OSError:
        pass


def _connect(db_path: Path) -> sqlite3.Connection:
    # Own timeout — do NOT inherit the persisted busy_timeout=0 (would fail-fast
    # under contention with the 6 live sessions' poll-hook writers).
    return sqlite3.connect(str(db_path), timeout=_CONN_TIMEOUT_S)


def ensure_indexes(db_path: Path) -> None:
    """Create the covering indexes GC + poll need so scans are O(log n).

    Idempotent (IF NOT EXISTS). Without these, every poll AND every GC pass is a
    full table scan over the inbox, lengthening the exclusive-lock window and
    risking poll-hook starvation. Safe to call on every GC.
    """
    con = _connect(db_path)
    try:
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_inbox_recipient_delivered "
            "ON inbox(recipient_id, delivered)"
        )
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_inbox_delivered_created "
            "ON inbox(delivered, created_at)"
        )
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_sessions_heartbeat "
            "ON sessions(last_heartbeat)"
        )
        con.commit()
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Honest counts (REPORTING half — used by bin/mesh `state`).
# ---------------------------------------------------------------------------

def _scalar(db_path: Path, sql: str, con: sqlite3.Connection | None = None) -> int:
    """Run a single-value COUNT query and return it as int (0 on no row).

    With *con* the caller owns the connection (and any transaction) — used by
    ``consistent_counts`` so all report reads share one snapshot (#3384).
    """
    if con is not None:
        row = con.execute(sql).fetchone()
        return int(row[0]) if row and row[0] is not None else 0
    own = _connect(db_path)
    try:
        row = own.execute(sql).fetchone()
        return int(row[0]) if row and row[0] is not None else 0
    finally:
        own.close()


def live_session_count(
    db_path: Path, window: str = REPORT_WINDOW, con: sqlite3.Connection | None = None
) -> int:
    """Count sessions truly alive within *window* (coerced; fixes BUG 2)."""
    return _scalar(
        db_path,
        f"SELECT COUNT(*) FROM sessions WHERE {live_session_predicate(window)}",
        con,
    )


def confirmed_live_session_count(
    db_path: Path, window: str = REPORT_WINDOW, con: sqlite3.Connection | None = None
) -> int:
    """Sessions live within *window* that have beaten at least once.

    The honest peer count: excludes rows that registered and never beat. Always
    <= ``live_session_count``; the difference is unconfirmed registrations.

    DEGRADES, never raises: a legacy/minimal ``sessions`` table with no
    ``registered_at`` column cannot express the beat test, so it falls back to
    ``live_session_count`` — reporting exactly as before this function existed
    rather than crashing a report on an old schema.
    """
    try:
        return _scalar(
            db_path,
            "SELECT COUNT(*) FROM sessions WHERE "
            f"{confirmed_live_session_predicate(window)}",
            con,
        )
    except sqlite3.OperationalError:
        return live_session_count(db_path, window, con)


def live_waiting_count(
    db_path: Path, window: str = REPORT_WINDOW, con: sqlite3.Connection | None = None
) -> int:
    """Count UNDELIVERED inbox rows addressed to a recipient alive within *window*.

    This is the honest "messages waiting" figure — it EXCLUDES dead-recipient
    tombstones. A mutation that reverts to raw `COUNT(*) WHERE delivered=0` will
    diverge from this and fail the Goodhart test.
    """
    return _scalar(
        db_path,
        "SELECT COUNT(*) FROM inbox WHERE delivered=0 AND recipient_id IN "
        f"(SELECT id FROM sessions WHERE {live_session_predicate(window)})",
        con,
    )


def tombstone_waiting_count(
    db_path: Path, window: str = REPORT_WINDOW, con: sqlite3.Connection | None = None
) -> int:
    """Count UNDELIVERED rows whose recipient is NOT alive within *window*.

    Surfaced as a SEPARATE, clearly-labelled "pending cleanup" diagnostic — never
    framed as current waiting work.
    """
    return _scalar(
        db_path,
        "SELECT COUNT(*) FROM inbox WHERE delivered=0 AND recipient_id NOT IN "
        f"(SELECT id FROM sessions WHERE {live_session_predicate(window)})",
        con,
    )


def live_snapshot_files(
    sync_dir: Path,
    db_path: Path,
    window: str = REPORT_WINDOW,
    con: sqlite3.Connection | None = None,
) -> list[Path]:
    """Return cc-*-LIVE.md files whose backing session is alive within *window*.

    Fixes the unfiltered glob: a snapshot counts as live only if its owning
    session id is in the live set. (mtime is a secondary signal handled by GC,
    not the live report.)
    """
    own: sqlite3.Connection | None = None
    if con is None:
        con = own = _connect(db_path)
    try:
        live_ids = {
            r[0]
            for r in con.execute(
                f"SELECT id FROM sessions WHERE {live_session_predicate(window)}"
            ).fetchall()
        }
    finally:
        if own is not None:
            own.close()
    out = []
    for f in sorted(sync_dir.glob("cc-*-LIVE.md")):
        sid = f.name[: -len("-LIVE.md")]
        if sid in live_ids:
            out.append(f)
    return out


def _table_exists(con: sqlite3.Connection, name: str) -> bool:
    """True iff *name* is a table in the connected db (no throw on an empty db)."""
    return con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def _file_based_counts(
    con: sqlite3.Connection, sync_dir: Path, window: str
) -> tuple[dict, list[Path]]:
    """Fallback when presence.db has no ``sessions`` table — the presence DB is
    empty/uninitialised (the node presence server never created the schema). The
    ``cc-*-LIVE.md`` dumps are the working ground truth, so derive liveness from
    their mtimes within *window* instead of crashing on the missing table. Keeps
    ``mesh state`` honest + functional; waiting counts are 0 (no inbox table to
    read). The window cutoff is computed by sqlite itself (``datetime('now',
    window)``) so any modifier form ('-1 hour', '-5 minutes') is honoured exactly
    without re-parsing it in Python."""
    row = con.execute(
        "SELECT strftime('%s', datetime('now', ?))", (window,)
    ).fetchone()
    cutoff = float(row[0]) if row and row[0] is not None else 0.0
    live = [
        f for f in sorted(sync_dir.glob("cc-*-LIVE.md"))
        if f.stat().st_mtime >= cutoff
    ]
    counts = {
        "live_sessions": len(live), "live_waiting": 0,
        "tombstone_waiting": 0, "live_snapshots": len(live),
    }
    return counts, live


def consistent_counts(
    db_path: Path, sync_dir: Path, window: str = REPORT_WINDOW
) -> tuple[dict, list[Path]]:
    """All four report reads on ONE connection inside ONE read transaction.

    #3384: four separate ``_connect`` calls were not a consistent snapshot — a
    concurrent reap between reads could skew one render (self-correcting but
    confusing) and multiplied busy_timeout exposure under 6+ sessions. A single
    deferred transaction gives every read the same view and one lock window.
    """
    con = _connect(db_path)
    try:
        if not _table_exists(con, "sessions"):
            # Empty/uninitialised presence.db — fall back to file-mtime liveness
            # so `mesh state` reports concurrent sessions instead of crashing on
            # `no such table: sessions` (the node presence server hasn't created
            # the schema). The LIVE.md dumps are the working ground truth.
            return _file_based_counts(con, sync_dir, window)
        con.execute("BEGIN")
        counts = {
            "live_sessions": live_session_count(db_path, window, con),
            # Same snapshot/connection — the confirmed figure must never be read
            # outside this transaction or it can disagree with the row above it.
            "confirmed_sessions": confirmed_live_session_count(db_path, window, con),
            "live_waiting": live_waiting_count(db_path, window, con),
            "tombstone_waiting": tombstone_waiting_count(db_path, window, con),
        }
        snapshots = live_snapshot_files(sync_dir, db_path, window, con)
        counts["live_snapshots"] = len(snapshots)
        con.rollback()  # read-only transaction — release the shared lock
        return counts, snapshots
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Reaper (DELETION half — patient, fail-closed, concurrency-safe).
# ---------------------------------------------------------------------------

def _chunked_delete(con: sqlite3.Connection, candidate_sql: str, params: tuple) -> int:
    """DELETE rows matching *candidate_sql* (an id-selecting SELECT) in chunks.

    Each chunk is one transaction holding the exclusive lock briefly, so live
    sessions' poll-hook UPDATEs are never starved past their timeout. The
    liveness sub-select lives INSIDE candidate_sql, so each chunk re-evaluates
    liveness atomically with its delete (no SELECT-then-DELETE window-edge race).
    Returns total rows deleted.
    """
    total = 0
    while True:
        cur = con.execute(
            f"DELETE FROM inbox WHERE id IN "
            f"(SELECT id FROM ({candidate_sql}) LIMIT {_CHUNK})",
            params,
        )
        con.commit()
        deleted = cur.rowcount if cur.rowcount is not None and cur.rowcount >= 0 else 0
        total += deleted
        if deleted < _CHUNK:
            break
    return total


def _reap_inbox(
    con: sqlite3.Connection, now_iso: str, audit: list[dict],
    ttl_msg_hours: int, ttl_recip_hours: int, ttl_delivered_hours: int,
) -> int:
    """Phase 1: collect undelivered tombstones (old AND recipient silent past
    TTL_RECIP) + aged delivered rows. Appends audit rows for tombstones. Returns
    rows deleted. The recipient-liveness sub-select uses the LOOSE window so an
    idle-but-alive recipient is spared (fail-closed)."""
    # datetime(created_at) coercion is MANDATORY here for the same reason as the
    # heartbeat liveness fix (BUG 2): created_at is stored 'T...Z' but
    # datetime('now',...) is space-separated, so a raw '<' sorts a 'T' row HIGHER
    # than the cutoff and wrongly judges an OLD message "too young" — leaving real
    # tombstones uncollectable forever. Coercing both sides compares true instants.
    recip_live = (
        "SELECT id FROM sessions WHERE "
        f"datetime(last_heartbeat) > datetime('now', '-{ttl_recip_hours} hours')"
    )
    tombstone_candidates = (
        "SELECT id FROM inbox WHERE delivered=0 "
        f"AND datetime(created_at) < datetime('now', '-{ttl_msg_hours} hours') "
        f"AND recipient_id NOT IN ({recip_live})"
    )
    to_log = con.execute(
        tombstone_candidates.replace(
            "SELECT id FROM inbox", "SELECT id, recipient_id, created_at FROM inbox"
        )
    ).fetchall()
    for rid, recipient, created in to_log:
        audit.append({
            "ts": now_iso, "action": "delete_inbox_tombstone",
            "row_id": rid, "recipient_id": recipient, "created_at": created,
        })
    deleted = _chunked_delete(con, tombstone_candidates, ())
    delivered_candidates = (
        "SELECT id FROM inbox WHERE delivered=1 "
        f"AND datetime(created_at) < datetime('now', '-{ttl_delivered_hours} hours')"
    )
    deleted += _chunked_delete(con, delivered_candidates, ())
    return deleted


def _session_delete_audit(now_iso: str, ids: list[str]) -> list[dict]:
    """Per-id audit rows for a chunk of deleted session ids. Extracted so the
    chunked reap loop in _reap_sessions carries no lexically-nested loop — total
    work stays O(n) (each id is touched exactly once across all chunks)."""
    return [
        {"ts": now_iso, "action": "delete_session", "session_id": sid} for sid in ids
    ]


def _reap_sessions(
    con: sqlite3.Connection, now_iso: str, audit: list[dict], ttl_session_hours: int,
) -> int:
    """Phase 2: delete session rows silent past TTL_SESSION (>= TTL_RECIP, so a
    recipient is never deleted while it could still own sparable inbox rows).
    Runs AFTER inbox so a session row's heartbeat stays available to judge its
    inbox rows' liveness. STREAMED — each chunk re-evaluates the dead-predicate
    INSIDE the DELETE and commits, so a backlog of >10^5 dead rows never
    materialises a Python id-list (bounded memory, parity with _chunked_delete).
    Audit is one batch-count row per chunk: session rows are ephemeral
    registration state, so the content-bearing trail is _reap_inbox's per-message
    audit, not per-session-id. Returns rows deleted."""
    dead_predicate = (
        "SELECT id FROM sessions WHERE "
        f"datetime(last_heartbeat) <= datetime('now', '-{ttl_session_hours} hours')"
    )
    deleted = 0
    while True:
        # Materialise only ONE chunk (<=_CHUNK ids) — bounded memory regardless of
        # backlog size, unlike the original full .fetchall() of every dead id.
        chunk = [r[0] for r in con.execute(f"{dead_predicate} LIMIT {_CHUNK}").fetchall()]
        if not chunk:
            break
        ph = ",".join("?" * len(chunk))
        # Re-filter the dead predicate INSIDE the DELETE: a session that heartbeat'd
        # between this SELECT and the DELETE no longer matches and is spared
        # (race-safe — never deletes a now-live recipient that could still own
        # sparable inbox rows).
        cur = con.execute(
            f"DELETE FROM sessions WHERE id IN ({ph}) "
            f"AND datetime(last_heartbeat) <= datetime('now', '-{ttl_session_hours} hours')",
            chunk,
        )
        con.commit()
        # Per-id audit (the logging contract): one record per dead session id in the
        # chunk, built via a helper so this loop is not lexically nested. A rare
        # raced-spared row is harmlessly re-evaluated next pass.
        audit.extend(_session_delete_audit(now_iso, chunk))
        deleted += cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
        if len(chunk) < _CHUNK:
            break
    return deleted


def _reap_snapshots(
    db_path: Path, sync_dir: Path, now_iso: str, audit: list[dict], ttl_snapshot_hours: int,
) -> int:
    """Phase 3: unlink cc-*-LIVE.md whose backing session is gone from the DB AND
    whose mtime is older than TTL_SNAPSHOT. A file whose session still exists is
    left to that session's own lifecycle. DB-independent of phases 1-2; last."""
    con = _connect(db_path)
    try:
        existing_ids = {r[0] for r in con.execute("SELECT id FROM sessions").fetchall()}
    finally:
        con.close()
    cutoff = time.time() - ttl_snapshot_hours * 3600
    deleted = 0
    for f in sorted(sync_dir.glob("cc-*-LIVE.md")):
        sid = f.name[: -len("-LIVE.md")]
        if sid in existing_ids:
            continue
        try:
            if f.stat().st_mtime >= cutoff:
                continue
            f.unlink()
            deleted += 1
            audit.append({"ts": now_iso, "action": "delete_snapshot", "file": f.name})
        except OSError:
            continue
    return deleted


def reap(
    db_path: Path,
    sync_dir: Path | None = None,
    *,
    ttl_msg_hours: int = TTL_MSG_HOURS,
    ttl_recip_hours: int = TTL_RECIP_HOURS,
    ttl_session_hours: int = TTL_SESSION_HOURS,
    ttl_delivered_hours: int = TTL_DELIVERED_HOURS,
    ttl_snapshot_hours: int = TTL_SNAPSHOT_HOURS,
    log_path: Path | None = None,
) -> dict[str, int]:
    """Run one GC pass (coordinator). Idempotent. Serves BOTH as the one-shot
    migration and the recurring reaper (same predicate — migration==structural).

    Strict order, fail-closed: (1) inbox tombstones + aged-delivered,
    (2) dead session rows, (3) stale LIVE.md. Every deletion audit-logged.
    Returns {'inbox': N, 'sessions': M, 'snapshots': K}.
    """
    if not db_path.exists():
        return {"inbox": 0, "sessions": 0, "snapshots": 0}

    ensure_indexes(db_path)
    audit: list[dict] = []
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")

    con = _connect(db_path)
    try:
        inbox_n = _reap_inbox(con, now_iso, audit, ttl_msg_hours, ttl_recip_hours, ttl_delivered_hours)
        sessions_n = _reap_sessions(con, now_iso, audit, ttl_session_hours)
    finally:
        con.close()

    snapshots_n = 0
    if sync_dir is not None and sync_dir.exists():
        snapshots_n = _reap_snapshots(db_path, sync_dir, now_iso, audit, ttl_snapshot_hours)

    _audit(audit, log_path)
    return {"inbox": inbox_n, "sessions": sessions_n, "snapshots": snapshots_n}


# ---------------------------------------------------------------------------
# Opportunistic throttle — lets the CLI call reap() cheaply on every `state`
# without GC'ing on every single invocation under 6 concurrent callers.
# ---------------------------------------------------------------------------

def _stamp_path(db_path: Path) -> Path:
    return db_path.parent / ".mesh-gc.stamp"


def maybe_reap(
    db_path: Path,
    sync_dir: Path | None = None,
    *,
    min_interval_s: int = 3600,
    **kwargs,
) -> dict[str, int] | None:
    """Run reap() only if the last run was more than *min_interval_s* ago.

    Uses an atomic O_CREAT|O_EXCL stamp acquisition so that under N concurrent
    sessions exactly ONE wins the interval and runs GC; the rest no-op. Returns
    the reap summary if it ran, else None. Never raises (best-effort hygiene).
    """
    if not db_path.exists():
        return None
    stamp = _stamp_path(db_path)
    now = time.time()
    try:
        if stamp.exists() and (now - stamp.stat().st_mtime) < min_interval_s:
            return None
    except OSError:
        return None
    # Atomic claim: touch mtime via O_EXCL temp + replace, so only one racer runs.
    try:
        stamp.parent.mkdir(parents=True, exist_ok=True)
        tmp = stamp.with_suffix(f".{os.getpid()}.tmp")
        fd = os.open(str(tmp), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        os.close(fd)
        os.replace(tmp, stamp)  # atomically refresh the stamp BEFORE running
    except FileExistsError:
        return None
    except OSError:
        return None
    try:
        return reap(db_path, sync_dir, **kwargs)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# CLI: `python3 lib/mesh_gc.py [reap|counts] [--once]`
# ---------------------------------------------------------------------------

def _default_db() -> Path:
    env = os.environ.get("CLAUDE_MESH_DB_PATH")
    return Path(env) if env else Path.home() / ".grip-session-mesh" / "presence.db"


def _default_sync() -> Path:
    env = os.environ.get("CLAUDE_MESH_SYNC_DIR")
    return Path(env) if env else Path.home() / ".claude" / "sync"


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="grip-session-mesh GC + honest counts")
    parser.add_argument("cmd", nargs="?", default="reap",
                        choices=["reap", "counts", "state-data"])
    parser.add_argument("--throttled", action="store_true",
                        help="reap only if >1h since last run (opportunistic)")
    args = parser.parse_args(argv)

    db = _default_db()
    sync = _default_sync()

    if args.cmd == "counts":
        counts, _ = consistent_counts(db, sync)
        print(json.dumps(counts))
        return 0

    if args.cmd == "state-data":
        # One-shot emitter for bin/mesh `state`: counts + live snapshot file paths,
        # so the CLI spawns ONE python process, not four. Line 1 = counts (k=v),
        # remaining lines = absolute live LIVE.md paths.
        counts, snapshots = consistent_counts(db, sync)
        print(
            f"live_sessions={counts['live_sessions']} "
            f"confirmed_sessions={counts['confirmed_sessions']} "
            f"live_waiting={counts['live_waiting']} "
            f"tombstone_waiting={counts['tombstone_waiting']}"
        )
        for p in snapshots:
            print(p)
        return 0

    summary = (maybe_reap(db, sync) if args.throttled else reap(db, sync)) or {
        "inbox": 0, "sessions": 0, "snapshots": 0, "skipped": True,
    }
    print(json.dumps(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
