#!/usr/bin/env python3
"""PreToolUse gate: refuse a subagent fan-out on a Pro plan before it spends anything.

WHY. A Pro plan has one Claude account and a small five-hour allowance. A
fan-out of several subagents spends it in minutes, and a warning cannot give
spent allowance back, so on a pro tier this hook refuses BEFORE the launch.
On max and api tiers a warning is enough. Operator decision, HITL 2026-10-15.

WHAT IT COUNTS. Every Agent/Task launch in a session, in
state/session/fanout-preflight-gate.json under CLAUDE_DIR (default ~/.claude),
keyed by session_id. A subagent's own launch carries an agent_id and its
parent's session_id, so nested spawns count against the same session. A
session idle for over a day starts over. The prediction for a launch is
(launches so far + this one) x 250,000 tokens (lib/fanout_preflight.py
DEFAULT_TOKENS_PER_AGENT), checked against the line for the declared tier
(fanout_preflight.threshold_for_tier): pro 250,000, so the first launch passes
and the second -- a fan-out of two -- is refused; max and api 1,000,000.

THE TIER is the one the operator declared: GRIP_TIER, else
state/grip-tier-config.json (`grip tier pro`), read by
grip_tier.resolve_configured(). Auto-detection never runs here: it reads the
macOS keychain through a subprocess. No declared tier means "unknown", and
unknown never refuses.

OVER THE LINE:
  pro                  -> permissionDecision "deny", with a reason that names
                          the override, and one line for the operator.
  max, api, unknown    -> no decision: additionalContext with the prediction
                          for the model, and a systemMessage for the operator
                          once per session.
Under the line the hook prints nothing.

OVERRIDES (they matter on pro only):
  GRIP_FANOUT_OVERRIDE=1 in Claude Code's environment lets every launch in
  that session through; set it when starting the session.
  A one-shot marker lets exactly ONE launch of ONE session through and is
  deleted when used. It is named for the session --
  state/session/fanout-override-<first 16 hex digits of sha256(session_id)> --
  so a session sharing this CLAUDE_DIR cannot spend another session's
  approval. The refusal prints the exact command; arm it from Bash, which this
  hook never gates:
      touch "${CLAUDE_DIR:-$HOME/.claude}/state/session/fanout-override-<key>"
  A marker older than OVERRIDE_TTL_SECONDS (10 minutes) is stale: it is
  removed and not honoured.

ESCAPE HATCH (rules/gate-resolution-escape-hatch.md, Invariant 1): the denied
set is Agent/Task launches on a declared pro tier past the line. Every
resolution lies outside it: the marker and `grip tier max` are Bash, the env
override is a session setting, and the work can run in the main session.

FAILS OPEN on every error path: an unreadable payload, a missing session id, a
failed import, an unwritable state file or any exception lets the launch
through. A malformed state file is replaced, so counting resumes on the next
launch. Launches from one message serialise on a lock file (flock on POSIX, a
byte lock through msvcrt on Windows), so they cannot all read a count of zero.
A launch another PreToolUse hook then refuses is still counted; the error is
towards refusing sooner, never later.

Audit: logs/fanout-preflight-gate.jsonl, one line per counted launch.
Tests: tests/test_fanout_preflight_gate_hook.py (named to match the *hook*
glob of the hooks-settings-registry CI block, so a pull request runs it).
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import sys
import time
from pathlib import Path

_LIB = Path(__file__).resolve().parent.parent / "lib"
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

try:
    import fanout_preflight as fp
    import grip_tier
    from claude_dir import get_claude_dir
    from hook_response import emit, pretool_context, pretool_decision
except Exception:  # noqa: BLE001 -- fail-open: a broken import must never block a launch
    sys.exit(0)

_DISPATCH_TOOLS = frozenset({"Agent", "Task"})
OVERRIDE_ENV = "GRIP_FANOUT_OVERRIDE"
OVERRIDE_TTL_SECONDS = 600
_STALE_SESSION_SECONDS = 86_400
_LOCK_WAIT_SECONDS = 2.0
_WINDOWS = os.name == "nt"


def _marker_name(session: str) -> str:
    """The session's one-shot marker file name: a digest, never the raw id."""
    return "fanout-override-" + hashlib.sha256(session.encode("utf-8")).hexdigest()[:16]


def _paths(session: str) -> dict:
    root = get_claude_dir()
    session_dir = root / "state" / "session"
    return {
        "state": session_dir / "fanout-preflight-gate.json",
        "lock": session_dir / "fanout-preflight-gate.lock",
        "marker": session_dir / _marker_name(session),
        "audit": root / "logs" / "fanout-preflight-gate.jsonl",
    }


def _try_lock(handle) -> bool:
    """Take an exclusive lock on the open lock file without waiting."""
    try:
        if _WINDOWS:
            import msvcrt
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def _unlock(handle) -> None:
    """Release a Windows byte lock; a POSIX flock goes when the file closes."""
    if not _WINDOWS:
        return
    try:
        import msvcrt
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    except OSError:
        pass


@contextlib.contextmanager
def _locked(lock_path: Path):
    """Hold an exclusive lock, waiting at most _LOCK_WAIT_SECONDS. If it cannot
    be had in time, carry on unlocked: fail open, never stall a launch."""
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+") as handle:
        deadline = time.monotonic() + _LOCK_WAIT_SECONDS
        held = _try_lock(handle)
        while not held and time.monotonic() < deadline:
            time.sleep(0.01)
            held = _try_lock(handle)
        try:
            yield
        finally:
            if held:
                _unlock(handle)


def _fresh(entry: object, now: float) -> bool:
    updated = entry.get("updated") if isinstance(entry, dict) else None
    return isinstance(updated, (int, float)) and now - updated < _STALE_SESSION_SECONDS


def _load(path: Path, now: float) -> dict:
    """The live sessions, or {} when the file is missing or malformed. Sessions
    idle for over a day are dropped HERE, before any verdict, so expiry never
    depends on which session happens to save next."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return {}
    sessions = data.get("sessions") if isinstance(data, dict) else None
    if not isinstance(sessions, dict):
        return {}
    return {sid: entry for sid, entry in sessions.items() if _fresh(entry, now)}


def _count(value: object) -> int:
    """A stored count, or 0 for anything that is not a non-negative int."""
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return max(value, 0)


def _save(path: Path, sessions: dict) -> None:
    """Write the table atomically."""
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps({"version": 1, "sessions": sessions}), encoding="utf-8")
    os.replace(tmp, path)


def _claim_override(marker: Path, now: float) -> str:
    """'env' for GRIP_FANOUT_OVERRIDE=1; 'marker' when this session's fresh
    one-shot marker was claimed (deleting it is the claim, so two launches
    cannot share one); '' otherwise. A stale marker is deleted unhonoured."""
    if os.environ.get(OVERRIDE_ENV) == "1":
        return "env"
    try:
        age = now - marker.stat().st_mtime
        marker.unlink()
    except FileNotFoundError:
        return ""
    return "marker" if age <= OVERRIDE_TTL_SECONDS else ""


def _verdict(tier: str | None, predicted: int, limit: int, marker: Path, now: float) -> str:
    if predicted <= limit:
        return "allow"
    if tier != "pro":
        return "warn"
    override = _claim_override(marker, now)
    return f"override-{override}" if override else "deny"


def _deny(numbers: tuple, marker: Path) -> dict:
    launches, predicted, limit = numbers
    arm = f'touch "${{CLAUDE_DIR:-$HOME/.claude}}/state/session/{marker.name}"'
    reason = (
        f"Fan-out refused on a Pro plan: this would be subagent launch {launches} in "
        f"this session, about {predicted:,} tokens ({launches} x "
        f"{fp.DEFAULT_TOKENS_PER_AGENT:,}), over the Pro line of {limit:,} (one agent). "
        "A Pro plan has one Claude account and a small five-hour allowance; a fan-out "
        "spends it in minutes, and a warning cannot give it back. To run this one task "
        f"anyway, arm this session's one-shot override from Bash: {arm} (it lets one "
        "launch through within 10 minutes). Or start the session with "
        f"{OVERRIDE_ENV}=1, or do the work in the main session. If this machine is not "
        "on Pro, declare its tier: grip tier max."
    )
    out = pretool_decision("deny", reason)
    out["systemMessage"] = (
        f"GRIP refused a subagent launch: Pro plan, launch {launches} in this session, "
        f"about {predicted:,} tokens. One-shot override for this session: {arm}"
    )
    return out


def _warning(tier: str | None, launches: int, predicted: int, limit: int) -> str:
    label = tier or "plan tier not declared"
    text = (
        f"Fan-out check ({label}): subagent launch {launches} in this session, about "
        f"{predicted:,} tokens predicted ({launches} x {fp.DEFAULT_TOKENS_PER_AGENT:,}), "
        f"over the {limit:,} warning line. This tier only warns; "
        "`grip-fanout-preflight --agents N` shows the live five-hour room first."
    )
    if tier is None:
        text += " Declare the plan tier with `grip tier pro|max|api`; on pro this refuses."
    return text


def _response(verdict: str, tier: str | None, numbers: tuple, first_warning: bool,
              marker: Path):
    launches, predicted, limit = numbers
    if verdict == "allow":
        return None
    if verdict == "deny":
        return _deny(numbers, marker)
    if verdict == "warn":
        text = _warning(tier, launches, predicted, limit)
        return pretool_context(text, text if first_warning else "")
    source = f"{OVERRIDE_ENV}=1" if verdict == "override-env" else "one-shot marker, now used up"
    return pretool_context(
        f"Fan-out override used ({source}): subagent launch {launches} in this session, "
        f"about {predicted:,} tokens on a Pro plan."
    )


def _decide(session: str, paths: dict) -> tuple:
    """Count this launch and decide, under the lock. Returns (verdict, tier,
    (launches, predicted, limit), first_warning)."""
    tier = grip_tier.resolve_configured()
    limit = fp.threshold_for_tier(tier)
    now = time.time()
    with _locked(paths["lock"]):
        sessions = _load(paths["state"], now)
        entry = sessions.get(session) if isinstance(sessions.get(session), dict) else {}
        launches = _count(entry.get("launches")) + 1
        predicted = fp.estimate_tokens(launches)
        verdict = _verdict(tier, predicted, limit, paths["marker"], now)
        warned = entry.get("warned") is True
        if verdict != "deny":
            sessions[session] = {"launches": launches, "updated": now,
                                 "warned": warned or verdict == "warn"}
            _save(paths["state"], sessions)
    return verdict, tier, (launches, predicted, limit), verdict == "warn" and not warned


def _audit(path: Path, record: dict) -> None:
    """Append one JSONL line; a lost audit line never blocks the launch."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        record["ts"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record) + "\n")
    except OSError as exc:
        print(f"[fanout-preflight-gate] audit write failed ({exc})", file=sys.stderr)


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:  # noqa: BLE001 -- fail-open on an unreadable payload
        return 0
    tool = payload.get("tool_name") if isinstance(payload, dict) else None
    session = payload.get("session_id") if tool in _DISPATCH_TOOLS else None
    if not isinstance(session, str) or not session:
        return 0
    paths = _paths(session)
    verdict, tier, numbers, first_warning = _decide(session, paths)
    _audit(paths["audit"], {
        "session": session, "tool": tool, "subagent": bool(payload.get("agent_id")),
        "tier": tier, "launches": numbers[0], "predicted_tokens": numbers[1],
        "limit_tokens": numbers[2], "verdict": verdict,
    })
    response = _response(verdict, tier, numbers, first_warning, paths["marker"])
    if response:
        emit(response)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # noqa: BLE001 -- fail-open: a hook defect must never block a launch
        sys.exit(0)
