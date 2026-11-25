"""P2 W2 — cross-session closeout advisory (the binding-DA inversion).

plans/unified-holistic-command.md W2 (CAREFUL). The hazard the binding-DA
dissent forbids: a *tempting close-others API* a future session wires into
direct foreign mutation (re-creating shared-.git/HEAD corruption BY
DESIGN). The correct-by-construction shape: there is **no close-others
verb, ever**. This module ONLY *writes a per-peer advisory file*; the
OWNING peer reads its own advisory on its own tick and runs its OWN
/closeout. The deciding+acting session is ALWAYS the owner.

Zero foreign mutation is STRUCTURAL here: this module imports/invokes no
git, no closeout, no process-kill — its only side effect is writing files
under ``state/session/closeout-advisory/``. And when run under /holistic
provenance, the W0 PreToolUse gate independently DENIES any git-mutation/
closeout (cross-wave backstop — defence in depth, not the primary).

No fabricated detection (the /ultraread lesson): `/whoa-update-pls` has
no importable lib API, so peer liveness uses the REAL primitive — ``pgrep -x``
over every binary in the harness registry (Rule 21). A peer is advised ONLY if
it is (a) not self, (b) a live pid, AND (c) carries an EXPLICIT closeable signal
in its own session marker. No signal ⇒ not advised (degrade-to-silence — never a
guessed classification).
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path
from typing import Optional

_CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", Path.home() / ".claude"))
_SESSION_DIR = _CLAUDE_DIR / "state" / "session"
_ADVISORY_DIR = _SESSION_DIR / "closeout-advisory"


def _harness_binaries() -> tuple:
    """Process names that count as a live session, from the picker registry.

    Falls back to the historical claude-only set when the registry cannot be
    imported, which keeps this module importable in a stripped environment
    without silently widening what it observes.
    """
    try:
        from grip_harness_picker import harness_binaries
    except ImportError:
        return ("claude",)
    return harness_binaries()


def _live_harness_pids() -> set[int]:
    """Live pids for EVERY harness gg++ can launch, not just `claude`.

    This was ``pgrep -x claude``. ``-x`` is an exact whole-name match, so a
    session running under codex, opencode, aider, hermes or agy had a live pid
    that simply did not appear here — ``closeable_peers`` then skipped it at the
    ``pid not in live`` check and the peer was never advised. The failure is
    silent by construction: a missing peer is indistinguishable from a peer that
    declined to signal, so the advisory under-reported with no error anywhere.

    Not hypothetical. On the machine this was written, ``pgrep -x claude``
    returned 6 pids while a codex session was also live — 7 real sessions, one
    invisible.

    Fail-safe empty on error, so nothing is advised rather than advising into an
    unobservable process table.
    """
    pids: set[int] = set()
    for binary in _harness_binaries():
        try:
            r = subprocess.run(["pgrep", "-x", binary],
                               capture_output=True, text=True, timeout=3)
        except (OSError, subprocess.TimeoutExpired, ValueError):
            return set()
        if r.returncode != 0:
            continue  # no process of that name — normal, not an error
        pids.update(int(p) for p in r.stdout.split() if p.strip().isdigit())
    return pids


def closeable_peers(root: Optional[Path] = None, *,
                    self_pid: Optional[int] = None) -> list[dict]:
    """READ-ONLY. Peers that are not-self, live, AND explicitly closeable.

    A per-session marker is closeable ONLY if it carries an explicit
    truthy ``closeable`` (or ``idle_done``) field — absent ⇒ NOT advised
    (degrade-to-silence; never a fabricated 'probably idle' guess)."""
    sess_dir = (Path(root) / "state" / "session") if root else _SESSION_DIR
    me = self_pid if self_pid is not None else os.getpid()
    live = _live_harness_pids()
    out: list[dict] = []
    if not sess_dir.is_dir():
        return out
    for mk in sorted(sess_dir.glob("session-*.json")):
        try:
            d = json.loads(mk.read_text())
        except (OSError, ValueError):
            continue  # unreadable ⇒ skip silently, never crash
        pid = d.get("pid")
        sid = d.get("session_id") or mk.stem
        if not isinstance(pid, int) or pid == me:
            continue  # own-echo never advised
        if pid not in live:
            continue  # stale / dead pid ignored
        if not (d.get("closeable") or d.get("idle_done")):
            continue  # no explicit signal ⇒ degrade-to-silence
        out.append({"session_id": sid, "pid": pid,
                    "reason": str(d.get("closeable_reason", "peer marked closeable"))})
    return out


def write_advisory(advisories: list[dict], *,
                   root: Optional[Path] = None) -> list[str]:
    """Write ONE advisory inbox file per peer (the peer self-consumes it on
    its own tick). Idempotent: identical advisory ⇒ identical file (no
    append-storm). The ONLY side effect of this module — no foreign
    mutation, ever, by construction."""
    adv_dir = (Path(root) / "state" / "session" / "closeout-advisory"
               ) if root else _ADVISORY_DIR
    adv_dir.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    for a in advisories:
        sid = a["session_id"]
        payload = {
            "session_id": sid, "pid": a["pid"], "reason": a["reason"],
            "advised_by": "/holistic", "advised_at": time.strftime(
                "%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "action": "the OWNING session decides + runs its own /closeout; "
                      "this is advisory only — no foreign mutation",
        }
        p = adv_dir / f"{sid}.json"
        # Idempotent: skip rewrite if the advisory (modulo timestamp) is
        # unchanged ⇒ no action-storm across repeated /holistic ticks.
        if p.exists():
            try:
                prev = json.loads(p.read_text())
                if {k: prev.get(k) for k in ("session_id", "pid", "reason")} == \
                   {k: payload[k] for k in ("session_id", "pid", "reason")}:
                    written.append(str(p))
                    continue
            except (OSError, ValueError):
                pass
        p.write_text(json.dumps(payload, indent=2))
        written.append(str(p))
    return written


def emit_closeout_advisory(root: Optional[Path] = None, *,
                           self_pid: Optional[int] = None) -> dict:
    """Detect closeable peers → write their advisory inboxes. READ-ONLY
    w.r.t. every peer (writes only advisory files; peers self-consume)."""
    peers = closeable_peers(root, self_pid=self_pid)
    written = write_advisory(peers, root=root)
    return {"advised": peers, "written": written, "count": len(peers)}
