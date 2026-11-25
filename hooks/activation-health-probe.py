#!/usr/bin/env python3
"""activation-health-probe.py — SessionStart hook (soft-crucial activation nag).

Fixes the Gen-635 bypass: first-run/deps detection lived only in `bin/grip`, so a
fresh `claude` launch could run with dead MCP servers and never notice (issue
#3896). This probe injects an honest [ACTIVATION REQUIRED] degraded banner when
GRIP has no recent PROOF that its own tools work.

It does NOT inspect servers itself — a filesystem check is unreliable (a node
server may need no node_modules, or keep its package dir nested; that false-nag is
the exact #3896 lesson). Instead it trusts the last **signed handshake receipt**
(state/activation/, written by the wizard Install phase / `grip-doctor --fix`).
So it never false-claims a healthy server is broken; it only asks the operator to
VERIFY when there is no fresh all-green proof.

It PRINTS context only — a SessionStart hook cannot deny a tool call, so it
informs + re-offers but can NEVER trap the operator (escape-hatch-safe by
construction). Silent once a fresh green receipt exists. Opt-out: env
GRIP_SKIP_ACTIVATION=1, or touch state/session/activation-skip.

Fail-safe: any internal error -> exit 0 silently (never block session start).
"""
import json
import os
import sys
import time
from pathlib import Path

_TRUTHY = ("1", "true", "yes", "on")
_MAX_AGE_DAYS = 7.0


def _skipped(home: Path, env) -> bool:
    if env.get("GRIP_SKIP_ACTIVATION", "").strip().lower() in _TRUTHY:
        return True
    return (home / "state" / "session" / "activation-skip").exists()


def probe_reason(home: Path, *, max_age_days: float = _MAX_AGE_DAYS, now_fn=time.time):
    """Reason to nag, or None when a fresh all-green receipt proves health.

    Trusts the signed handshake receipt — never re-inspects servers (that is the
    wizard/grip-doctor's job, via a real live handshake).
    """
    receipt_dir = home / "state" / "activation"
    receipts = sorted(receipt_dir.glob("receipt-*.json")) if receipt_dir.is_dir() else []
    if not receipts:
        return "GRIP has not verified its own tools yet"
    newest = receipts[-1]
    try:
        signed = json.loads(newest.read_text())
    except (OSError, ValueError):
        return "GRIP's activation receipt is unreadable"
    if not signed.get("receipt", {}).get("all_green"):
        return "GRIP's last check found offline tools"
    age_days = (now_fn() - newest.stat().st_mtime) / 86400.0
    if age_days > max_age_days:
        return f"GRIP's tools were last verified {int(age_days)} days ago"
    return None


def banner(reason: str) -> str:
    return (
        f"[ACTIVATION REQUIRED] {reason}.\n"
        "GRIP may be running DEGRADED (some skills can silently fail their tool calls).\n"
        "Fix + verify in one command:  grip-doctor --fix   "
        "(installs deps, heals + verifies every server live, signs a receipt).\n"
        "Escape hatch (silence this): export GRIP_SKIP_ACTIVATION=1"
    )


def main(*, home: Path | None = None, env=None, now_fn=time.time) -> int:
    env = env if env is not None else os.environ
    if home is None:
        home = Path(os.environ.get("CLAUDE_DIR") or (Path.home() / ".claude"))
    if _skipped(home, env):
        return 0
    reason = probe_reason(home, now_fn=now_fn)
    if reason:
        print(banner(reason), flush=True)
    return 0


if __name__ == "__main__":
    try:
        main()
    except Exception:  # noqa: BLE001 - never block session start
        sys.exit(0)
