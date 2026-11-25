#!/usr/bin/env python3
"""Operator-session resume routing for ``lib/relay_dispatcher.py``.

The relay dispatcher historically spawns one short-lived ``claude -p`` per
inbound channel event, persisting a *per-channel* session id at
``state/relay/sessions/<channel>.txt`` so consecutive messages on the same
channel share a sub-conversation lineage. That sub-lineage is owned by the
*relay*, not the *operator's foreground REPL* — so a WhatsApp answer from
V>>'s relay never lands in the history he is actively typing into.

This module adds a second, *operator-scoped*, opt-in resume target. A
SessionStart hook stamps the operator's current Claude Code session id (the
project-level ``CLAUDE_SESSION_ID``) plus a wall-clock timestamp into
``state/relay/operator-session-id.txt``. The dispatcher reads that file
before spawning. If the stamp is fresh (≤ TTL, default 8h) the spawn uses
``--resume <operator-sid> --fork-session`` so the relay forks from the
operator's history without contending for the live REPL's session lock.

Three soundness invariants (each tested):

1. **Atomic write** — temp-file + ``os.replace`` so a concurrent reader
   never sees a half-written stamp.
2. **TTL-bounded fail-closed** — any read error, missing file, malformed
   JSON, or stale stamp returns ``None``; the caller falls back to the
   existing per-channel resume (worst case ⇒ current behaviour).
3. **No capability widening** — this module only ever returns a session
   id (or ``None``). It cannot influence the dispatcher's ``--tools``
   allowlist; the capability lock lives in
   ``relay_dispatcher.build_argv`` and is tested independently.

Why ``--fork-session``: ``claude --resume <uuid>`` against a session id
that another live ``claude`` process holds open is undefined-by-contract
(may race, may overwrite, may refuse). ``--fork-session`` creates a *new*
session id seeded from the resumed history — so the relay can read the
operator's context without ever touching the operator's writeable session
state. The operator's REPL is reader-immutable from the relay's vantage.

Stdlib only — same syscall-doctrine constraint as relay_dispatcher.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

# Default operator-session stamp path. Resolved lazily via ``stamp_path()`` so
# tests can override ``GRIP_RELAY_CLAUDE_CWD`` without re-importing.
_REL_PATH = ("state", "relay", "operator-session-id.txt")

# Default TTL: 8h = one working day. Long enough that V>> can leave a session
# open during the day; short enough that a stale stamp from an overnight
# session doesn't quietly route messages into a dead session.
_DEFAULT_TTL_SECONDS = 8 * 60 * 60

# Operator-armed routing marker (task #405 — /rc arming without SSH). Sibling
# of the stamp file: its EXISTENCE (not content) arms operator-session resume
# routing for the consumer process that reads it — the same read-fresh-per-
# spawn pattern as the auth-failover marker (relay_dispatcher.
# AUTH_FAILOVER_MARKER), so a local file flip takes effect on the consumer's
# NEXT spawn with no env change and no pm2 restart. Content is audit metadata
# only ({armed_at, source}); a consumer never parses it.
_MARKER_REL_PATH = ("state", "relay", "operator-session-routing.json")


def _claude_dir() -> Path:
    return Path(
        os.environ.get("GRIP_RELAY_CLAUDE_CWD", os.path.expanduser("~/.claude"))
    )


def stamp_path() -> Path:
    """Absolute path of the operator-session stamp file."""
    return _claude_dir().joinpath(*_REL_PATH)


def _ttl_seconds() -> int:
    """TTL window — env-overridable for tests + operator tuning."""
    raw = os.environ.get("GRIP_RELAY_OPERATOR_SESSION_TTL_SEC")
    if not raw:
        return _DEFAULT_TTL_SECONDS
    try:
        n = int(raw)
        return n if n > 0 else _DEFAULT_TTL_SECONDS
    except (ValueError, TypeError):
        return _DEFAULT_TTL_SECONDS


def write_stamp(session_id: str, *, now: float | None = None,
                cwd: str | None = None) -> None:
    """Atomically persist ``session_id`` + wall-clock to the stamp file.

    Uses temp-file + ``os.replace`` so a reader can never observe a partial
    write. The stamp body is a one-line JSON object (forward-compatible —
    extra fields are ignored by ``read_active_session_id``).

    ``cwd`` is the operator session's PROJECT DIRECTORY. Claude Code session
    ids are project-scoped: ``claude -p --resume <sid>`` only finds the
    session when spawned from the same project dir the session ran in
    (live miss 2026-07-13: a worktree session's sid was unresolvable from
    the dispatcher's ``~/.claude`` cwd → every fork spawn exited 1). Callers
    that know their cwd (the SessionStart tracker) pass it; a stamp without
    cwd keeps the pre-fix spawn behaviour.
    """
    sid = (session_id or "").strip()
    if not sid:
        return  # never write an empty stamp (would shadow a real prior stamp)
    path = stamp_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "session_id": sid,
        "stamped_at": int(now if now is not None else time.time()),
    }
    if cwd and str(cwd).strip():
        payload["cwd"] = str(cwd).strip()
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(payload, separators=(",", ":")) + "\n",
                   encoding="utf-8")
    os.replace(tmp, path)


def read_active_stamp(*, now: float | None = None) -> dict | None:
    """Return the fresh stamp as ``{"session_id": str, "cwd": str | None}``,
    or ``None`` on any miss.

    Misses (all degrade to caller's fallback path — never raise):
      * stamp file does not exist
      * stamp file is unreadable / empty / not JSON
      * stamp payload lacks ``session_id`` / ``stamped_at``
      * stamped_at older than TTL (stale)
      * stamped_at in the future (clock skew / corruption — treat as stale)

    ``cwd`` is None for pre-cwd stamps (forward-compat: old stamps stay
    valid; the consumer just spawns from its default cwd as before).
    """
    path = stamp_path()
    try:
        raw = path.read_text(encoding="utf-8").strip()
    except (FileNotFoundError, PermissionError, OSError):
        return None
    if not raw:
        return None
    try:
        payload = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if not isinstance(payload, dict):
        return None
    sid = payload.get("session_id")
    stamped_at = payload.get("stamped_at")
    if not isinstance(sid, str) or not sid.strip():
        return None
    if not isinstance(stamped_at, (int, float)):
        return None
    current = now if now is not None else time.time()
    age = current - stamped_at
    if age < 0:  # future-dated stamp ⇒ treat as stale
        return None
    if age > _ttl_seconds():
        return None
    cwd = payload.get("cwd")
    cwd = cwd.strip() if isinstance(cwd, str) and cwd.strip() else None
    return {"session_id": sid.strip(), "cwd": cwd}


def read_active_session_id(*, now: float | None = None) -> str | None:
    """Back-compat sid-only view of ``read_active_stamp`` (same miss rules)."""
    stamp = read_active_stamp(now=now)
    return stamp["session_id"] if stamp else None


def marker_path() -> Path:
    """Absolute path of the operator-armed routing marker."""
    return _claude_dir().joinpath(*_MARKER_REL_PATH)


def arm(source: str = "manual", *, now: float | None = None) -> Path:
    """Arm operator-session routing by writing the marker (atomic).

    ``source`` is audit metadata only ("skill", "wa-control", "manual") —
    consumers test existence, never content. Same temp+``os.replace`` write
    contract as ``write_stamp``. Errors propagate (an arm that did not land
    must be LOUD, never a silent success). Returns the marker path.
    """
    path = marker_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "armed_at": int(now if now is not None else time.time()),
        "source": str(source or "manual"),
    }
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(payload, separators=(",", ":")) + "\n",
                   encoding="utf-8")
    os.replace(tmp, path)
    return path


def disarm() -> bool:
    """Remove the routing marker. True iff a marker was removed.

    ``FileNotFoundError`` → False (already disarmed — idempotent). Any OTHER
    OSError propagates: a disarm that leaves the marker in place must be
    LOUD, never an acked-but-false success. The env flag (deployment-level
    switch in the consumer's own env) is deliberately NOT touched here.
    """
    try:
        marker_path().unlink()
        return True
    except FileNotFoundError:
        return False


def is_marker_armed() -> bool:
    """True iff the routing marker exists.

    Read FRESH per call — the consumer is a long-lived daemon; caching would
    recreate the restart-to-apply problem the marker exists to remove.
    Fail-safe: an unreadable path answers False (routing stays off).
    """
    try:
        return marker_path().exists()
    except OSError:
        return False


def is_enabled() -> bool:
    """Whether operator-session resume routing is enabled.

    Two arming surfaces, either one suffices (both checked fresh per call):

    * env flag ``GRIP_RELAY_OPERATOR_SESSION_ROUTING`` — deployment-level
      (e.g. ``~/.claude/.grip-channel.env``; needs a process
      restart to change — the pre-#405 only path);
    * the marker at ``marker_path()`` — operator-level (``/rc`` arm): a
      local file flip the consumer honours on its NEXT spawn, no restart,
      no SSH (mirrors the auth-failover marker pattern).

    Opt-in default OFF for safety: with neither surface set, a
    freshly-deployed dispatcher never silently changes its resume target
    and the per-channel path is byte-identical to prior behaviour.
    """
    if os.environ.get(
            "GRIP_RELAY_OPERATOR_SESSION_ROUTING", "").strip() in ("1", "true", "yes"):
        return True
    return is_marker_armed()
