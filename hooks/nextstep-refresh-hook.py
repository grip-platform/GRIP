#!/usr/bin/env python3
"""nextstep-refresh-hook — PreToolUse + periodic Hook Push for unified next_up.

§4.3: rate-limited (default 60s, env GRIP_NEXTSTEP_REFRESH_INTERVAL_S).
On each non-rate-limited tick:
  1. publish this session's own fresh unified snapshot (long-running
     self-reread path — requirement (b));
  2. consume_pending — if an in-scope peer's next_up changed, emit the
     fresh unified "## Next Step" to STDERR (the established live-inject
     surface; a running model cannot reload its own context —
     feedback_closed_loop_push_based_via_posttooluse).

Fail-open ABSOLUTE: ANY error / missing dep / timeout ⇒ silent exit 0.
Never blocks a tool call, never delays. Mirrors holistic_synthesiser's
"notification must never break the caller" R3 contract.
"""

import os
import sys
import time
from pathlib import Path

CLAUDE_DIR = Path.home() / ".claude"
_STAMP = CLAUDE_DIR / "state" / "session" / "nextstep-refresh.stamp"
_DEFAULT_INTERVAL_S = 60


def _rate_limited() -> bool:
    """True if a refresh ran within the interval; else stamp now + False.

    Fail-open: a stat/write error never blocks (returns False / proceeds).
    """
    try:
        interval = int(
            os.environ.get("GRIP_NEXTSTEP_REFRESH_INTERVAL_S", _DEFAULT_INTERVAL_S)
        )
    except (TypeError, ValueError):
        interval = _DEFAULT_INTERVAL_S
    interval = max(5, interval)  # clamp: a <=0 interval makes the mtime
    # check always-False → never rate-limited → fires every PreToolUse
    # (busy path). 5s floor. HAL #2917 "what I'd falsify" (negative interval).
    try:
        if _STAMP.exists() and (time.time() - _STAMP.stat().st_mtime) < interval:
            return True
    except OSError:
        return False
    try:
        _STAMP.parent.mkdir(parents=True, exist_ok=True)
        _STAMP.write_text(str(time.time()))
    except OSError:
        pass
    return False


def _session_count() -> str:
    try:
        import subprocess
        mesh = CLAUDE_DIR / "bin" / "mesh"
        if not mesh.exists():
            return "?"
        r = subprocess.run(
            [str(mesh), "list"], timeout=2,
            capture_output=True, text=True, check=False,
        )
        if r.returncode == 0 and r.stdout:
            return str(max(1, len([ln for ln in r.stdout.splitlines() if ln.strip()])))
    except Exception:
        pass
    return "?"


def _fast_rail_sync(nextup_mesh, project_path: Path) -> None:
    """Candidate A cross-machine fast rail (opt-in via GRIP_NEXTUP_ENDPOINT).

    Unset ⇒ byte-identical prior behaviour = the #2917 git-sync floor (same
    reversibility contract as GRIP_RELAY_WEBHOOK_URL). POSTs this session's
    snapshot + pulls peers into the local partition so the consume step sees
    the freshest cross-machine state. Best-effort; never raises.
    """
    endpoint = os.environ.get("GRIP_NEXTUP_ENDPOINT", "").strip()
    if not endpoint:
        return
    try:
        import nextup_xfer  # noqa: E402
        nextup_xfer.sync(project_path, endpoint)
    except Exception:
        pass  # fast rail best-effort; #2917 floor covers every miss


def _refresh_nextstep(nextup_mesh, project_path: Path) -> None:
    """Publish own snapshot, then surface a fresh '## Next Step' if stale.

    Peer-change first (consume_pending); if silent, the single-session
    self-staleness path (consume_self_refresh) the peer gate is structurally
    blind to — e.g. an overnight RSI loop whose boot-time block went stale.
    A peer emit already shows the freshest unified (subsuming own changes), so
    on the peer path we only stamp-sync the self state to avoid a duplicate
    re-emit next tick. Emits to STDERR (the live-inject surface).
    """
    # publish returns the freshly-computed own digest — reuse it for the self
    # staleness gate so the self path adds NO synthesise on the busy hook
    # (BIG-O; synthesise is O(instances×filesize)). publish + consume_pending
    # keep their own internal compute, unmodified. Fail-open: digest None on
    # any publish error → the self helpers fall back to a lazy compute / no-op.
    digest = None
    try:
        digest = nextup_mesh.publish_unified_next_up(project_path)
    except Exception:
        pass  # self-publish best-effort; consume still runs
    _fast_rail_sync(nextup_mesh, project_path)
    lines = nextup_mesh.consume_pending(project_path)
    if lines:
        nextup_mesh.mark_self_surfaced(project_path, digest)
    else:
        lines = nextup_mesh.consume_self_refresh(project_path, digest=digest)
    if lines:
        sys.stderr.write(
            f"\n[GRIP NEXT-STEP — unified, {_session_count()} session(s)]\n"
            + "\n".join(lines) + "\n"
        )


def main() -> None:
    try:
        try:
            sys.stdin.read(65536)  # BOUNDED — never block/OOM on a large
            # tool stdin payload; PreToolUse must not delay (content
            # irrelevant to this hook). HAL #2917 concern 1, fixed.
        except Exception:
            pass
        if _rate_limited():
            sys.exit(0)
        if str(CLAUDE_DIR / "lib") not in sys.path:
            sys.path.insert(0, str(CLAUDE_DIR / "lib"))
        import nextup_mesh  # noqa: E402  (also pulls next_step_block)
        _refresh_nextstep(nextup_mesh, Path(os.getcwd()))
    except SystemExit:
        raise
    except BaseException:
        pass  # fail-open ABSOLUTE — never break the tool call
    sys.exit(0)


if __name__ == "__main__":
    main()
