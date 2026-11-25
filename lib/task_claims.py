"""Task Claims -- host-local mechanical lease for shared harness tasks (#2795).

Solves: N Claude sessions on ONE machine each read the SAME shared task pool,
independently compute "the next valuable item", and start it N times (the
2026-07-12/13 collisions -- #3944 duplicated #3943, #3982 reconcile-to-complement,
the multi-session /back start). All were same-host races.

Relationship to the behavioural rule (rules/task-lease-claim-before-work.md,
shipped #4002): that rule is the L1 protocol -- claim on the harness task
``owner`` field, read-back-verify, 4h TTL. Read-back-verify has a TOCTOU window
because the harness owns the task store and last-writer-wins the ``owner`` field.
This module is the L2 mechanical enforcement (#436/#2795): a host-local claim
over its OWN registry guarded by an atomic ``FileLock``, so when two same-host
sessions call ``claim`` concurrently exactly one wins -- true mutual exclusion
(an atomic read-modify-write under a host-local ``FileLock``) that the
read-back-verify cannot guarantee. The mesh-backed cross-machine lease the rule
names remains the L3 escalation (still deferred; all observed collisions were
same-host).

Mirrors lib/plan_claims.py (session-scoped, TTL, FileLock, CLI) and adds:
  * TTL default 2h (D3:A/2h -- a task lease is shorter-lived than a plan's 24h).
  * Liveness expiry -- a claim is ALSO free if its owning session is provably
    dead, freeing a crashed session's task before its TTL. "Provably dead" =
    the session transcript (~/.claude/projects/*/<session_id>.jsonl) exists but
    has not been appended to within the idle window. Fail-safe toward ALIVE: an
    indeterminate liveness (no transcript, probe error) NEVER frees a claim --
    only positive death evidence or TTL does. Freeing a live session's claim
    would re-introduce the very collision this exists to stop, so uncertainty
    always keeps the claim.

State file: ~/.claude/cache/task-claims.json
Lock: FileLock on the state file (sidecar mode)

Usage:
    from task_claims import TaskClaims

    claims = TaskClaims()
    if claims.claim("436", session_id="uuid-1"):
        # Work the task...
        claims.release("436", session_id="uuid-1")   # on done/abandon

CLI:
    python3 lib/task_claims.py claim <task_id> <session_id> [ttl_hours]
    python3 lib/task_claims.py is-claimed <task_id> [exclude_session]
    python3 lib/task_claims.py release <task_id> <session_id>
    python3 lib/task_claims.py cleanup
    python3 lib/task_claims.py list
"""

import json
import os
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional

_lib_dir = str(Path(__file__).resolve().parent)
if _lib_dir not in sys.path:
    sys.path.insert(0, _lib_dir)

from file_lock import FileLock

CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", Path.home() / ".claude"))
CLAIMS_FILE = CLAUDE_DIR / "cache" / "task-claims.json"
PROJECTS_DIR = CLAUDE_DIR / "projects"

DEFAULT_TTL_HOURS = 2

# A session that has not appended to its transcript within this window is
# treated as dead by the liveness probe. Conservative (90 min) so a live-but-
# idle session -- one waiting on a long tool call -- is never wrongly freed;
# TTL (2h) is the backstop for every other case. Env-overridable for tuning.
LIVENESS_IDLE_SECONDS = int(os.environ.get("GRIP_TASK_CLAIM_IDLE_SEC", 90 * 60))


def _transcript_liveness(
    session_id: str, projects_dir: Optional[Path] = None,
    idle_seconds: int = LIVENESS_IDLE_SECONDS,
) -> Optional[bool]:
    """Liveness of a session by its transcript mtime.

    Returns True (recently active), False (transcript exists but stale =>
    provably dead), or None (indeterminate: no session id, no transcript, or a
    probe error). ``None`` and ``True`` both keep a claim; only ``False`` frees
    it -- so uncertainty is fail-safe toward the claim being held.
    """
    if not session_id:
        return None
    base = projects_dir or PROJECTS_DIR
    try:
        matches = list(base.glob(f"*/{session_id}.jsonl"))
        if not matches:
            return None
        newest = max(m.stat().st_mtime for m in matches)
    except OSError:
        return None
    return (time.time() - newest) <= idle_seconds


class TaskClaims:
    """Session-scoped, host-local task lease manager."""

    def __init__(
        self, claims_path: Optional[Path] = None,
        alive_fn: Optional[Callable[[str], Optional[bool]]] = None,
    ):
        self.path = claims_path or CLAIMS_FILE
        # Injectable so tests exercise every liveness verdict without the FS.
        self._alive_fn = alive_fn or _transcript_liveness

    def _load(self) -> dict:
        if not self.path.exists():
            return {"claims": {}}
        try:
            with open(self.path) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return {"claims": {}}

    def _save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(self.path.parent), suffix=".tmp")
        try:
            os.write(fd, json.dumps(data, indent=2).encode())
            os.close(fd)
            os.replace(tmp, self.path)
        except Exception:
            try:
                os.close(fd)
            except OSError:
                pass
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def _is_active(self, claim: dict) -> bool:
        """A claim is active unless its TTL elapsed OR its owner is provably dead."""
        if self._ttl_elapsed(claim.get("expires_at", "")):
            return False
        # Only a definitive False frees the claim; None/True keep it (fail-safe).
        return self._alive_fn(claim.get("session_id", "")) is not False

    @staticmethod
    def _ttl_elapsed(expires_at: str) -> bool:
        """True if the ISO-8601 expiry is in the past.

        Parses to a timezone-aware datetime and compares by INSTANT, not by
        lexical string order -- so an expiry carrying a non-UTC offset (e.g.
        ``+05:00``) is judged correctly rather than sorting wrongly against a
        UTC ``now``. A missing expiry is never elapsed (liveness governs); a
        malformed one is treated as elapsed so a corrupt claim can never block.
        """
        if not expires_at:
            return False
        try:
            return datetime.fromisoformat(expires_at) <= datetime.now(timezone.utc)
        except (ValueError, TypeError):
            return True

    def claim(self, task_id: str, session_id: str, ttl_hours: int = DEFAULT_TTL_HOURS) -> bool:
        """Claim a task for this session.

        Returns True if acquirable -- unclaimed, expired, owner dead, or the
        same session re-claiming (which also refreshes the TTL). Returns False
        only if a LIVE other session holds it.
        """
        with FileLock(self.path, timeout=5, sidecar=True):
            data = self._load()
            claims = data.get("claims", {})

            existing = claims.get(task_id)
            same_session = existing and existing.get("session_id") == session_id
            if existing and not same_session and self._is_active(existing):
                return False  # live claim by another session

            now = datetime.now(timezone.utc)
            claims[task_id] = {
                "session_id": session_id,
                "claimed_at": (existing or {}).get("claimed_at", now.isoformat())
                if same_session else now.isoformat(),
                "expires_at": _add_hours(now, ttl_hours),
                "status": "claimed",
            }
            data["claims"] = claims
            self._save(data)
            return True

    def is_claimed(self, task_id: str, exclude_session: Optional[str] = None) -> Optional[dict]:
        """Return the active claim on a task, or None (unclaimed/expired/dead/own)."""
        with FileLock(self.path, timeout=5, sidecar=True, shared=True):
            data = self._load()
            claim = data.get("claims", {}).get(task_id)
            if not claim or not self._is_active(claim):
                return None
            if exclude_session and claim.get("session_id") == exclude_session:
                return None
            return claim

    def release(self, task_id: str, session_id: str) -> bool:
        """Release a claim (only the claiming session can release)."""
        with FileLock(self.path, timeout=5, sidecar=True):
            data = self._load()
            claim = data.get("claims", {}).get(task_id)
            if not claim or claim.get("session_id") != session_id:
                return False
            del data["claims"][task_id]
            self._save(data)
            return True

    def cleanup_expired(self) -> int:
        """Remove expired/dead claims. Returns count removed."""
        if not self.path.exists():
            return 0
        with FileLock(self.path, timeout=5, sidecar=True):
            data = self._load()
            claims = data.get("claims", {})
            if not claims:
                return 0
            stale = [tid for tid, c in claims.items() if not self._is_active(c)]
            for tid in stale:
                del claims[tid]
            if stale:
                self._save(data)
            return len(stale)

    def list_claims(self) -> dict:
        """Return all active claims (expired/dead excluded)."""
        with FileLock(self.path, timeout=5, sidecar=True, shared=True):
            data = self._load()
            return {tid: c for tid, c in data.get("claims", {}).items()
                    if self._is_active(c)}


def _add_hours(dt: datetime, hours: int) -> str:
    """Add hours to datetime, return ISO string."""
    return (dt + timedelta(hours=hours)).isoformat()


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: task_claims.py <command> [args]", file=sys.stderr)
        return 1

    cmd = sys.argv[1]
    claims = TaskClaims()

    if cmd == "claim" and len(sys.argv) >= 4:
        task_id, session_id = sys.argv[2], sys.argv[3]
        ttl = int(sys.argv[4]) if len(sys.argv) > 4 else DEFAULT_TTL_HOURS
        ok = claims.claim(task_id, session_id, ttl)
        print(json.dumps({"claimed": ok, "task_id": task_id}))
        return 0 if ok else 1

    elif cmd == "is-claimed" and len(sys.argv) >= 3:
        task_id = sys.argv[2]
        exclude = sys.argv[3] if len(sys.argv) > 3 else None
        info = claims.is_claimed(task_id, exclude_session=exclude)
        if info:
            print(json.dumps({"claimed": True, **info}))
            return 0
        print(json.dumps({"claimed": False}))
        return 1

    elif cmd == "release" and len(sys.argv) >= 4:
        ok = claims.release(sys.argv[2], sys.argv[3])
        print(json.dumps({"released": ok}))
        return 0 if ok else 1

    elif cmd == "cleanup":
        count = claims.cleanup_expired()
        print(json.dumps({"cleaned": count}))
        return 0

    elif cmd == "list":
        print(json.dumps(claims.list_claims(), indent=2))
        return 0

    else:
        print(f"Unknown command: {cmd}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
