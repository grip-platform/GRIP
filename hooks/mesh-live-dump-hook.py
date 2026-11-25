#!/usr/bin/env python3
"""
mesh-live-dump-hook.py — PostToolUse hook: periodic per-session live-state snapshot.

Rewrites ~/.claude/sync/<session-id>-LIVE.md with current git + process + mesh state
on every tool call, subject to a rate limit (default 300 s between writes).

Rate-limit semantics: if the output file exists and its mtime is less than
GRIP_MESH_DUMP_INTERVAL_S seconds ago, the hook exits 0 without rewriting.
This prevents excessive I/O on fast tool-call sequences while still providing
near-real-time state visibility to consumers.

Output base dir: env CLAUDE_MESH_SYNC_DIR, else ~/.claude/sync/.
File is OVERWRITTEN each cycle (not appended) — consumers always see the
latest snapshot at write time.

Hypothesis H-MESH-DUMP-1: consumers reading <session-id>-LIVE.md always see
state no older than GRIP_MESH_DUMP_INTERVAL_S seconds, enabling cross-session
live discovery. Falsified if the file is absent or stale beyond 2× the interval
during an active session.

Stdlib only: subprocess, os, pathlib, datetime, sqlite3, time, sys, socket.
"""

import os
import sqlite3
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Canonical session-identity resolver (task #520 — de-dup the inline copy). The
# LIVE path is lib/mesh_identity.resolve_session_id, the single source of truth
# pinned by tests/test_mesh_identity.py. Fail-open to an inline last-resort mirror
# so a missing import can NEVER break this hook (stdlib-only, never blocks).
try:
    _MESH_LIB = str(Path(__file__).resolve().parent.parent / "lib")
    if _MESH_LIB not in sys.path:
        sys.path.insert(0, _MESH_LIB)
    from mesh_identity import resolve_session_id as _canon_session_id
except Exception:  # pragma: no cover - import-failure last resort only
    def _canon_session_id() -> str:
        raw = (os.environ.get("CLAUDE_CODE_SESSION_ID")
               or os.environ.get("CLAUDE_SESSION_ID") or "").strip()
        return f"cc-{raw}" if raw else f"cc-pid-{os.getppid()}"


# ---------------------------------------------------------------------------
# Session identity (canonical resolver — task #520)
# ---------------------------------------------------------------------------

def _session_fields() -> tuple[str, str, str]:
    # session_id via the canonical resolver (task #520); see the module-level
    # import for the fail-open contract.
    session_id = _canon_session_id()
    name = os.environ.get("GRIP_SESSION_NAME") or Path.cwd().name or "unknown"
    machine = socket.gethostname()
    return session_id, name, machine


# ---------------------------------------------------------------------------
# Rate limit
# ---------------------------------------------------------------------------

def _should_skip(output_path: Path, interval: float) -> bool:
    try:
        return output_path.exists() and (time.time() - output_path.stat().st_mtime) < interval
    except OSError:
        return False


# ---------------------------------------------------------------------------
# Git helpers
# ---------------------------------------------------------------------------

def _git(args: list[str], cwd: str) -> str:
    try:
        r = subprocess.run(
            ["git"] + args, cwd=cwd,
            capture_output=True, text=True, timeout=2,
        )
        return r.stdout.strip() if r.returncode == 0 else ""
    except Exception:
        return ""


def _git_state(cwd: str) -> dict:
    branch = _git(["rev-parse", "--abbrev-ref", "HEAD"], cwd) or "n/a"
    head_sha = _git(["rev-parse", "--short", "HEAD"], cwd) or "n/a"
    status_out = _git(["status", "--porcelain"], cwd)
    dirty = bool(status_out)
    dirty_count = len(status_out.splitlines()) if dirty else 0
    log_lines = _git(["log", "--oneline", "-5"], cwd).splitlines()
    return {
        "drift": _drift(cwd),
        "branch": branch,
        "head_sha": head_sha,
        "dirty": dirty,
        "dirty_count": dirty_count,
        "log": log_lines,
    }


def _drift(cwd: str) -> dict:
    """How far this session sits from origin/main, and how far to trust that.

    Deliberately never fetches. This runs on every tool call, so a network
    round-trip is out of the question. The counts are measured against the
    LOCAL origin/main ref, which only advances on fetch — so `behind` is a
    LOWER BOUND, never an overcount, and the age of the ref travels beside
    it so the number is never read as fresher than it is. A session that
    has not fetched in hours can report behind=0 while being far behind;
    `ref_age_s` is what makes that visible instead of silently reassuring.
    """
    behind = _git(["rev-list", "--count", "HEAD..origin/main"], cwd)
    ahead = _git(["rev-list", "--count", "origin/main..HEAD"], cwd)
    if not behind.isdigit():
        # No origin/main ref at all (no remote, or never fetched).
        return {"behind": None, "ahead": None, "ref_age_s": None}

    ref_age_s = None
    common = _git(["rev-parse", "--git-common-dir"], cwd)
    if common:
        base = Path(common)
        if not base.is_absolute():
            base = Path(cwd) / base
        # A fresh clone has neither FETCH_HEAD nor a LOOSE origin/main ref —
        # the ref is inside packed-refs. Omitting that source made the age
        # read as unknown in the commonest case of all.
        for candidate in (
            base / "FETCH_HEAD",
            base / "refs/remotes/origin/main",
            base / "packed-refs",
        ):
            try:
                ref_age_s = int(time.time() - candidate.stat().st_mtime)
                break
            except OSError:
                continue

    return {
        "behind": int(behind),
        "ahead": int(ahead) if ahead.isdigit() else 0,
        "ref_age_s": ref_age_s,
    }


def _drift_phrase(d: dict) -> str:
    """One human-readable token for the frontmatter and the digest."""
    if d["behind"] is None:
        return "unknown"

    age = d["ref_age_s"]
    if age is None:
        stale = ", ref age unknown"
    elif age > 3600:
        stale = f", ref {age // 3600}h old"
    else:
        stale = ""

    # A bare "level" is a claim of freshness. Only make it when the ref was
    # demonstrably fetched recently; otherwise carry the caveat, because an
    # unqualified zero is the reassurance this field exists to refuse.
    if d["behind"] == 0:
        return f"level{stale}" if stale else "level"
    return f"behind>={d['behind']}{stale}"


# ---------------------------------------------------------------------------
# Mesh presence check
# ---------------------------------------------------------------------------

def _db_path() -> Path:
    """Presence DB path — env override else the canonical ~/.grip-session-mesh."""
    env_path = os.environ.get("CLAUDE_MESH_DB_PATH")
    return Path(env_path) if env_path else Path.home() / ".grip-session-mesh" / "presence.db"


def _mesh_registered(session_id: str, db_path: Path) -> str:
    try:
        with sqlite3.connect(str(db_path), timeout=1) as conn:
            row = conn.execute("SELECT 1 FROM sessions WHERE id=?", (session_id,)).fetchone()
        return "yes" if row else "no"
    except Exception:
        return "no"


# ---------------------------------------------------------------------------
# Autonomous mesh-GC heartbeat
# ---------------------------------------------------------------------------

def _opportunistic_gc(db_path: Path, sync_dir: Path) -> None:
    """Fire the throttled mesh GC so the dead-recipient tombstone backlog drains
    autonomously.

    Root cause this closes: mesh_gc.maybe_reap (the >=1h, single-winner reaper) was
    only ever triggered by a MANUAL `bin/mesh state|collect|sync`. With no operator
    running those, the 24h TTL went unenforced and dead-recipient inbox tombstones
    (one per broadcast recipient that dies before draining), dead session rows, and
    stale LIVE.md files accreted unbounded — 2315 inbox + 276 sessions + 218
    snapshots observed 2026-06-18. The reaper existed and was correct; it simply had
    no autonomous trigger (lifecycle-closed-or-expired: a TTL with no reaper that
    actually fires is no TTL).

    This hook is the one thing every active session runs on a timer, so it is the
    natural heartbeat. maybe_reap self-throttles (>=1h, atomic O_EXCL stamp), so N
    sessions firing this on every tool call do ~ONE real reap per hour; the rest
    stat the stamp and return. Fail-closed: any import/DB error is swallowed — GC is
    hygiene, never load-bearing for the snapshot this hook exists to write."""
    try:
        import importlib.util
        gc_path = Path(__file__).resolve().parent.parent / "lib" / "mesh_gc.py"
        # spec_from_file_location loads the EXACT adjacent mesh_gc.py WITHOUT touching
        # global sys.path (the codebase idiom — cf. tests/test_mesh_gc.py). mesh_gc has
        # only stdlib imports, so no lib/ needs to be on the path for it to load.
        spec = importlib.util.spec_from_file_location("mesh_gc", gc_path)
        if spec is None or spec.loader is None:
            return
        mesh_gc = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mesh_gc)
        mesh_gc.maybe_reap(db_path, sync_dir)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Markdown render
# ---------------------------------------------------------------------------

def _render(session_id: str, name: str, machine: str, cwd: str, git: dict, mesh: str) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    dirty_str = "true" if git["dirty"] else "false"
    dirty_label = f"yes, {git['dirty_count']} files" if git["dirty"] else "no, 0 files"

    log_block = "\n".join(f"  - {line}" for line in git["log"]) or "  (none)"

    return (
        f"---\n"
        f"session_id: {session_id}\n"
        f"name: {name}\n"
        f"machine: {machine}\n"
        f"cwd: {cwd}\n"
        f"branch: {git['branch']}\n"
        f"head_sha: {git['head_sha']}\n"
        f"dirty: {dirty_str}\n"
        f"drift: {_drift_phrase(git['drift'])}\n"
        f"timestamp: {ts}\n"
        f"---\n"
        f"# Live Session State: {name}\n\n"
        f"## Git\n"
        f"- Branch: {git['branch']}\n"
        f"- HEAD: {git['head_sha']}\n"
        f"- Dirty: {dirty_label}\n"
        f"- Distance from origin/main: {_drift_phrase(git['drift'])}\n"
        f"- Recent commits:\n{log_block}\n\n"
        f"## Process\n"
        f"- PID: {os.getppid()}\n"
        f"- Hostname: {machine}\n"
        f"- Mesh registered: {mesh}\n\n"
        f"## Pointer\n"
        f"This file is overwritten every 5 minutes (configurable via "
        f"GRIP_MESH_DUMP_INTERVAL_S). It reflects the snapshot AT WRITE TIME "
        f"— read it to see the latest known state of this session.\n"
    )


# ---------------------------------------------------------------------------
# Atomic write
# ---------------------------------------------------------------------------

def _atomic_write(path: Path, content: str) -> None:
    tmp = Path(str(path) + ".tmp")
    try:
        tmp.write_text(content, encoding="utf-8")
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    interval = float(os.environ.get("GRIP_MESH_DUMP_INTERVAL_S", "300"))

    env_dir = os.environ.get("CLAUDE_MESH_SYNC_DIR")
    sync_dir = Path(env_dir) if env_dir else Path.home() / ".claude" / "sync"
    sync_dir.mkdir(parents=True, exist_ok=True)

    session_id, name, machine = _session_fields()
    output_path = sync_dir / f"{session_id}-LIVE.md"
    db_path = _db_path()

    # Autonomous mesh-GC heartbeat (H888). Runs BEFORE the dump rate-limit below so
    # GC gets a chance on every tool call even when the 5-min snapshot is skipped;
    # maybe_reap's own >=1h single-winner throttle governs the real cadence. Without
    # this, the reaper only ever ran on a manual `bin/mesh state` and the backlog
    # accreted unbounded. Fail-closed inside the helper.
    _opportunistic_gc(db_path, sync_dir)

    if _should_skip(output_path, interval):
        return

    cwd = str(Path.cwd())
    git = _git_state(cwd)
    mesh = _mesh_registered(session_id, db_path)
    content = _render(session_id, name, machine, cwd, git, mesh)
    _atomic_write(output_path, content)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
