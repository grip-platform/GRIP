"""Plan Claims -- session-scoped plan ownership to prevent cross-session contamination.

Solves: two sessions independently executing the same preplan (wasting tokens).
Mechanism: claim-before-execute with TTL-based expiry.

State file: ~/.claude/cache/plan-claims.json
Lock: FileLock on the state file (sidecar mode)

Usage:
    from plan_claims import PlanClaims

    claims = PlanClaims()
    if claims.claim("my-plan", session_id="uuid-1"):
        # Execute plan...
        claims.mark_executed("my-plan", session_id="uuid-1")

CLI:
    python3 lib/plan_claims.py claim <plan_id> <session_id>
    python3 lib/plan_claims.py is-claimed <plan_id>
    python3 lib/plan_claims.py release <plan_id> <session_id>
    python3 lib/plan_claims.py executed <plan_id> <session_id>
    python3 lib/plan_claims.py cleanup
    python3 lib/plan_claims.py archive [--max-age-days 30]
"""

import json
import os
import shutil
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

_lib_dir = str(Path(__file__).resolve().parent)
if _lib_dir not in sys.path:
    sys.path.insert(0, _lib_dir)

from file_lock import FileLock

CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", Path.home() / ".claude"))
CLAIMS_FILE = CLAUDE_DIR / "cache" / "plan-claims.json"
PLANS_DIR = CLAUDE_DIR / "plans"
ARCHIVE_DIR = PLANS_DIR / "archive"

DEFAULT_TTL_HOURS = 24
ARCHIVE_AGE_DAYS = 30


class PlanClaims:
    """Session-scoped plan claim manager."""

    def __init__(self, claims_path: Optional[Path] = None):
        self.path = claims_path or CLAIMS_FILE

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

    def claim(self, plan_id: str, session_id: str, ttl_hours: int = DEFAULT_TTL_HOURS) -> bool:
        """Claim a plan for this session. Returns False if already claimed by another."""
        with FileLock(self.path, timeout=5, sidecar=True):
            data = self._load()
            claims = data.get("claims", {})

            existing = claims.get(plan_id)
            if existing:
                # Same session re-claiming is OK
                if existing.get("session_id") == session_id:
                    return True
                # Check if expired
                expires = existing.get("expires_at", "")
                if expires and expires > datetime.now(timezone.utc).isoformat():
                    return False  # Active claim by another session

            now = datetime.now(timezone.utc)
            claims[plan_id] = {
                "session_id": session_id,
                "claimed_at": now.isoformat(),
                "expires_at": _add_hours(now, ttl_hours),
                "status": "claimed",
            }
            data["claims"] = claims
            self._save(data)
            return True

    def is_claimed(self, plan_id: str, exclude_session: Optional[str] = None) -> Optional[dict]:
        """Check if plan is claimed. Returns claim info or None."""
        with FileLock(self.path, timeout=5, sidecar=True, shared=True):
            data = self._load()
            claim = data.get("claims", {}).get(plan_id)
            if not claim:
                return None
            # Expired?
            expires = claim.get("expires_at", "")
            if expires and expires <= datetime.now(timezone.utc).isoformat():
                return None
            # Exclude own session?
            if exclude_session and claim.get("session_id") == exclude_session:
                return None
            # Already executed?
            if claim.get("status") == "executed":
                return claim  # Return but caller can check status
            return claim

    def release(self, plan_id: str, session_id: str) -> bool:
        """Release a claim (only the claiming session can release)."""
        with FileLock(self.path, timeout=5, sidecar=True):
            data = self._load()
            claim = data.get("claims", {}).get(plan_id)
            if not claim or claim.get("session_id") != session_id:
                return False
            del data["claims"][plan_id]
            self._save(data)
            return True

    def mark_executed(self, plan_id: str, session_id: str) -> bool:
        """Mark a claimed plan as executed."""
        with FileLock(self.path, timeout=5, sidecar=True):
            data = self._load()
            claim = data.get("claims", {}).get(plan_id)
            if not claim or claim.get("session_id") != session_id:
                return False
            claim["status"] = "executed"
            claim["executed_at"] = datetime.now(timezone.utc).isoformat()
            self._save(data)
            return True

    def cleanup_expired(self) -> int:
        """Remove expired claims. Returns count removed."""
        if not self.path.exists():
            return 0
        with FileLock(self.path, timeout=5, sidecar=True):
            data = self._load()
            claims = data.get("claims", {})
            if not claims:
                return 0
            now = datetime.now(timezone.utc).isoformat()
            expired = [pid for pid, c in claims.items()
                       if c.get("expires_at", "") <= now]
            for pid in expired:
                del claims[pid]
            if expired:
                self._save(data)
            return len(expired)

    def archive_old_plans(self, max_age_days: int = ARCHIVE_AGE_DAYS) -> int:
        """Move plan files older than max_age_days to archive/. Returns count archived."""
        if not PLANS_DIR.exists():
            return 0
        ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
        cutoff = time.time() - (max_age_days * 86400)
        archived = 0
        for plan_file in PLANS_DIR.glob("*.md"):
            if plan_file.stat().st_mtime < cutoff:
                dest = ARCHIVE_DIR / plan_file.name
                shutil.move(str(plan_file), str(dest))
                archived += 1
        return archived

    def list_claims(self) -> dict:
        """List all active claims."""
        with FileLock(self.path, timeout=5, sidecar=True, shared=True):
            data = self._load()
            now = datetime.now(timezone.utc).isoformat()
            active = {}
            for pid, claim in data.get("claims", {}).items():
                if claim.get("expires_at", "") > now:
                    active[pid] = claim
            return active


def _add_hours(dt: datetime, hours: int) -> str:
    """Add hours to datetime, return ISO string."""
    return (dt + timedelta(hours=hours)).isoformat()


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: plan_claims.py <command> [args]", file=sys.stderr)
        return 1

    cmd = sys.argv[1]
    claims = PlanClaims()

    if cmd == "claim" and len(sys.argv) >= 4:
        plan_id, session_id = sys.argv[2], sys.argv[3]
        ttl = int(sys.argv[4]) if len(sys.argv) > 4 else DEFAULT_TTL_HOURS
        ok = claims.claim(plan_id, session_id, ttl)
        print(json.dumps({"claimed": ok, "plan_id": plan_id}))
        return 0 if ok else 1

    elif cmd == "is-claimed" and len(sys.argv) >= 3:
        plan_id = sys.argv[2]
        exclude = sys.argv[3] if len(sys.argv) > 3 else None
        info = claims.is_claimed(plan_id, exclude_session=exclude)
        if info:
            print(json.dumps({"claimed": True, **info}))
            return 0
        else:
            print(json.dumps({"claimed": False}))
            return 1

    elif cmd == "release" and len(sys.argv) >= 4:
        ok = claims.release(sys.argv[2], sys.argv[3])
        print(json.dumps({"released": ok}))
        return 0 if ok else 1

    elif cmd == "executed" and len(sys.argv) >= 4:
        ok = claims.mark_executed(sys.argv[2], sys.argv[3])
        print(json.dumps({"executed": ok}))
        return 0 if ok else 1

    elif cmd == "cleanup":
        count = claims.cleanup_expired()
        print(json.dumps({"cleaned": count}))
        return 0

    elif cmd == "archive":
        days = int(sys.argv[2]) if len(sys.argv) > 2 else ARCHIVE_AGE_DAYS
        count = claims.archive_old_plans(days)
        print(json.dumps({"archived": count}))
        return 0

    elif cmd == "list":
        active = claims.list_claims()
        print(json.dumps(active, indent=2))
        return 0

    else:
        print(f"Unknown command: {cmd}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
