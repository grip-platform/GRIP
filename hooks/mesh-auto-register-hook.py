#!/usr/bin/env python3
"""
mesh-auto-register-hook.py — keeps the current Claude Code session's row in
the grip-session-mesh presence DB for exactly as long as the session lives.
Wired into SessionStart (register the moment a session opens), PostToolUse
(refresh last_heartbeat so the session stays "live" while it works) and
SessionEnd with ``--session-end`` (retire the row when the session ends).

Why SessionEnd, measured on Claude Code 2.1.283. A session's id ends in three
ways, and each fires SessionEnd for the ENDING id, in the payload's
``session_id``: ``/clear`` and ``/resume`` (reason ``clear`` / ``resume``; the
same process carries on under a new id), and process exit (``prompt_input_exit``,
``logout``, ``other``). A headless ``claude -p`` run fires it too, with reason
``other`` (probed). Before this, nothing retired a row: on 2026-10-01 ten of
fifteen rows belonged to ids superseded by /clear or to runs that had ended.
Claude Code's own record of a process's earlier ids, ``formerNames`` in
``sessions/<pid>.json``, is a NAME history — an entry is written only when a
name changes — so it cannot stand in for this. A process that is killed never
reaches SessionEnd; its row waits for lib/mesh_gc.py, and liveness checks keep
messages away from it meanwhile.

OPT-OUT, never opt-in (V>> PARAMOUNT 2026-06-22,
feedback_grip_presence_mesh_always_on.md): every GRIP session joins the
presence mesh by default, headless runs included — they now leave it when they
end. Set GRIP_MESH_OPTOUT=1 (or true/yes/on) to suppress registration entirely
for that session.

Idempotence: first call INSERTs, subsequent calls UPDATE last_heartbeat only
(registered_at is preserved — the upsert is keyed by session id, so a re-fire
updates an existing row, never duplicates it). Retiring a row that is already
gone is a no-op. Fail-open: exits 0 silently in every error/opt-out/
missing-daemon condition so it can NEVER block or slow session start or exit.
A presence registration is best-effort coordination, not a prerequisite for
the session to run.

Hypothesis H-MESH-AUTOREG-1: all CC sessions (opt-out unset) appear in
presence.db within 1 second of SessionStart, enabling cross-session mesh
discovery without any manual registration step.
"""

import json
import os
import sqlite3
import socket
import sys
from datetime import datetime, timezone
from pathlib import Path

# Canonical session-identity resolver (task #520 — de-dup the inline copy). The
# LIVE path is lib/mesh_identity.resolve_session_id, the single source of truth
# pinned by tests/test_mesh_identity.py. Fail-open to an inline last-resort mirror
# so a missing import can NEVER break session start (this hook advertises "stdlib
# only, never blocks"). The mirror reproduces the exact cc-{raw}/cc-pid-{ppid}
# contract; the test's whitespace + both-unset cases pin both paths.
try:
    _MESH_LIB = str(Path(__file__).resolve().parent.parent / "lib")
    if _MESH_LIB not in sys.path:
        sys.path.insert(0, _MESH_LIB)
    from mesh_identity import resolve_session_id as _canon_session_id
except Exception:  # pragma: no cover - import-failure last resort only
    def _canon_session_id(env=None) -> str:
        src = os.environ if env is None else env
        raw = (src.get("CLAUDE_CODE_SESSION_ID") or src.get("CLAUDE_SESSION_ID") or "").strip()
        return f"cc-{raw}" if raw else f"cc-pid-{os.getppid()}"

_OPTOUT_TRUE = {"1", "true", "yes", "on"}

# Canonical presence schema — mirrors presence/src/db.ts (the MCP server's DDL).
# Run here (idempotent) so registration SELF-HEALS on any machine where the
# presence MCP server never ran to create it. That was the live bug: presence.db
# was a 0-byte file with no `sessions` table, so every INSERT threw
# `no such table: sessions` and failed silently via main()'s bare except — the
# mesh showed 0 live sessions despite many running. CREATE TABLE IF NOT EXISTS is
# a no-op once the tables exist, so this never conflicts with the server's DDL.
_DDL = (
    "CREATE TABLE IF NOT EXISTS sessions ("
    "id TEXT PRIMARY KEY, name TEXT NOT NULL, machine TEXT, role TEXT, "
    "pair_partner TEXT, registered_at TEXT NOT NULL, last_heartbeat TEXT NOT NULL);"
    "CREATE TABLE IF NOT EXISTS locks ("
    "resource TEXT PRIMARY KEY, holder_id TEXT NOT NULL, holder_name TEXT NOT NULL, "
    "acquired_at TEXT NOT NULL, expires_at TEXT NOT NULL);"
    "CREATE TABLE IF NOT EXISTS inbox ("
    "id TEXT PRIMARY KEY, recipient_id TEXT NOT NULL, from_name TEXT NOT NULL, "
    "body TEXT NOT NULL, kind TEXT NOT NULL, created_at TEXT NOT NULL, "
    "delivered INTEGER NOT NULL DEFAULT 0);"
)

# How long the SessionEnd path waits for the harness payload before giving up.
_PAYLOAD_WAIT_S = 0.2


def _opted_out() -> bool:
    """True when the operator has explicitly opted this session out of the mesh."""
    return os.environ.get("GRIP_MESH_OPTOUT", "").strip().lower() in _OPTOUT_TRUE


def _resolve_db_path() -> Path | None:
    """Return DB path from env or default; None if the file does not exist."""
    env_path = os.environ.get("CLAUDE_MESH_DB_PATH")
    db = Path(env_path) if env_path else Path.home() / ".grip-session-mesh" / "presence.db"
    return db if db.exists() else None


def _build_session_fields() -> tuple[str, str, str]:
    """Return (session_id, name, machine) from environment."""
    # session_id via the canonical resolver (task #520); see the module-level
    # import for the fail-open contract.
    session_id = _canon_session_id()
    name = os.environ.get("GRIP_SESSION_NAME") or Path.cwd().name or "unknown"
    machine = socket.gethostname()
    return session_id, name, machine


def _upsert(db_path: Path, session_id: str, name: str, machine: str) -> None:
    """Upsert session row; preserves registered_at on conflict."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    sql = (
        "INSERT INTO sessions(id, name, machine, role, pair_partner, registered_at, last_heartbeat) "
        "VALUES(?,?,?,?,NULL,?,?) "
        "ON CONFLICT(id) DO UPDATE SET last_heartbeat=excluded.last_heartbeat"
    )
    with sqlite3.connect(str(db_path), timeout=2) as conn:
        conn.executescript(_DDL)  # self-heal: ensure schema exists before upsert
        conn.execute(sql, (session_id, name, machine, "grip", now, now))
        conn.commit()


def _read_payload() -> dict:
    """The harness's JSON payload on stdin, or {} -- never blocks.

    Only the SessionEnd path reads it: a PostToolUse payload carries the whole
    tool output, so the register and heartbeat paths never touch stdin. Claude
    Code writes the payload and closes the pipe; a tty, or a pipe that never
    delivers (a test runner's inherited stdin), must not hold up session exit,
    so POSIX waits at most _PAYLOAD_WAIT_S for data. select() cannot wait on a
    pipe on Windows, where the read is direct (the harness closes the pipe).
    """
    try:
        if sys.stdin is None or sys.stdin.isatty():
            return {}
        if os.name != "nt":
            import select  # lazy: only the SessionEnd path pays for it
            if not select.select([sys.stdin], [], [], _PAYLOAD_WAIT_S)[0]:
                return {}
        payload = json.loads(sys.stdin.read() or "{}")
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _retire(db_path: Path, session_id: str) -> None:
    """Delete the ended session's row. Its undelivered inbox rows are left for
    lib/mesh_gc.py, which already collects messages to recipients that are gone."""
    with sqlite3.connect(str(db_path), timeout=2) as conn:
        conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
        conn.commit()


def _retire_ended_session(db_path: Path) -> None:
    """Retire the row of the session the SessionEnd payload names.

    The payload's id, never this process's environment: at /clear the hook
    runs for the OLD id while the same process is about to carry on under a
    new one. Without an id in the payload nothing is retired, rather than
    guessing and deleting a live session's row.
    """
    raw = str(_read_payload().get("session_id") or "").strip()
    if raw:
        _retire(db_path, _canon_session_id({"CLAUDE_CODE_SESSION_ID": raw}))


def main() -> None:
    if _opted_out():
        sys.exit(0)
    db_path = _resolve_db_path()
    if db_path is None:
        # Daemon DB absent / mesh unreachable — fail open, never block start.
        sys.exit(0)
    if "--session-end" in sys.argv[1:]:
        _retire_ended_session(db_path)
        return
    session_id, name, machine = _build_session_fields()
    _upsert(db_path, session_id, name, machine)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
