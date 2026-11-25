#!/usr/bin/env python3
"""
PostToolUse hook: GRIP session-mesh inbox surfacing.

On every tool call, reads pending inbox messages addressed to this session
from ~/.grip-session-mesh/presence.db and injects them into the session's
context as PostToolUse ``additionalContext`` on STDOUT, with a copy on stderr
for the human transcript.

Hypothesis H-MESH-POLL-1 -- "inbox surfacing via stderr causes mesh requests to
reach the active session within one tool-call round-trip" -- is REFUTED,
measured 2026-09-21. This hook printed only to stderr, and Claude Code
populates a hook attachment's ``content`` from STDOUT. Measured over a real
5,748-attachment transcript: 22 MESH-INBOX attachments, every one carrying
``content: ""`` with the text present only in ``stderr``; the 6 hook
attachments that did reach the model all satisfied ``content == stdout``.

The failure was worse than silence. ``_fetch_and_mark`` committed
``delivered=1`` BEFORE the print loop ran, so every broadcast was drained,
flagged as delivered, and discarded without any model ever seeing it. The
hook's own falsification clause named this outcome -- "if messages
consistently fail to surface (stderr not forwarded by the runtime) ... this
hook is ineffective" -- so the refutation is recorded here rather than argued.

Two changes follow from that measurement, and they are separable:

1. Emission moves to STDOUT as ``hookSpecificOutput.additionalContext``,
   mirroring ``hooks/tool-error-self-check.py::_emit_warn`` -- the pattern
   whose text is observed reaching a model. The stderr copy is retained: it is
   what a human reads in the terminal, and it costs nothing.
2. SELECT and UPDATE are split (``_fetch_pending`` / ``_mark_delivered``), and
   the mark runs only AFTER a successful emit. A crash between the two now
   re-delivers on the next tool call instead of destroying the message.
   At-least-once beats at-most-once for an advisory that blocks nothing.

Delivery remains PULL, and that is a property of the transport, not a defect:
a session idling at a prompt takes no tool calls, so this hook does not run and
that session cannot be reached at all. Nothing here changes that.

Session ID resolution: CLAUDE_CODE_SESSION_ID (the harness UUID) wins, then
CLAUDE_SESSION_ID (legacy / manual override), then the unstable pid fallback --
see lib/mesh_identity.py, which is the canonical order. Naming only the legacy
variable here was not cosmetic: three test suites were written against this
line, set the second-priority variable, inherited the first from the live
shell, and could only pass where no session was running. Resolution is, else
f"cc-pid-{os.getppid()}" -- matches mesh-auto-register-hook.py convention.

DB path: CLAUDE_MESH_DB_PATH env, else ~/.grip-session-mesh/presence.db.
Absent DB -> exit 0 (mesh not initialised, not an error).

CC: max 4 per function. Stdlib only.
"""

import json
import os
import sqlite3
import sys
from pathlib import Path

# Canonical session-identity resolver (task #520 — de-dup the inline copy). The
# LIVE path is lib/mesh_identity.resolve_session_id, the single source of truth
# pinned by tests/test_mesh_identity.py. Fail-open to an inline last-resort mirror
# so a missing import can NEVER break the hook (stdlib-only, never blocks).
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


def _resolve_session_id() -> str:
    # Canonical resolver (task #520); see the module-level import for the
    # fail-open contract. Name preserved for the mesh-identity parity test.
    return _canon_session_id()


def _resolve_db_path() -> Path | None:
    env_path = os.environ.get("CLAUDE_MESH_DB_PATH", "")
    if env_path:
        p = Path(env_path)
        return p if p.exists() else None
    default = Path.home() / ".grip-session-mesh" / "presence.db"
    return default if default.exists() else None


def _hook_payload() -> dict:
    """The PostToolUse payload Claude Code writes to stdin, or {} when there is none.

    A terminal or an absent stream means a manual run, which is treated as the
    main session -- exactly what this hook did before it read its input at all.
    """
    stream = sys.stdin
    if stream is None or stream.isatty():
        return {}
    try:
        data = json.loads(stream.read() or "{}")
    except (ValueError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def _from_background_agent(payload: dict) -> bool:
    """True when the tool call that fired this hook was made by a background agent.

    Background agents inherit their parent's session id, so the inbox query
    cannot tell them apart from the parent; only the payload's ``agent_id`` can
    (hooks/quality-posttool-hook.py records the same fact, task #342). Without
    this check, whichever caller made a tool call first drained the parent's
    inbox, and the message landed in a background transcript nobody reads:
    measured 2026-10-01 in fleet run wf_1dc2a6cb-980, where rows addressed to
    the main session were marked delivered by its lane agents. The main
    session's payload carries no ``agent_id``, so its delivery is unchanged.
    """
    agent_id = payload.get("agent_id")
    return isinstance(agent_id, str) and bool(agent_id.strip())


def _fetch_pending(db_path: Path, session_id: str) -> list[tuple[str, str, str, str]]:
    """Read undelivered rows. Deliberately does NOT mark them.

    Marking is deferred to _mark_delivered so that a failure between reading and
    emitting re-delivers the message rather than destroying it.
    """
    con = sqlite3.connect(str(db_path), timeout=2)
    try:
        cur = con.execute(
            "SELECT id, from_name, body, kind FROM inbox "
            "WHERE recipient_id=? AND delivered=0 "
            "ORDER BY created_at ASC LIMIT 10",
            (session_id,),
        )
        return cur.fetchall()
    finally:
        con.close()


def _mark_delivered(db_path: Path, ids: list[str]) -> None:
    """Flag rows delivered. Called ONLY after the emit has succeeded."""
    if not ids:
        return
    con = sqlite3.connect(str(db_path), timeout=2)
    try:
        placeholders = ",".join("?" * len(ids))
        con.execute(f"UPDATE inbox SET delivered=1 WHERE id IN ({placeholders})", ids)
        con.commit()
    finally:
        con.close()


def _emit(lines: list[str]) -> None:
    """Inject on STDOUT; mirror to stderr for the human transcript.

    STDOUT is the load-bearing channel -- it is what populates the attachment's
    ``content`` and therefore the only one a model reads. The stderr copy is a
    convenience and must never be the sole emission.
    """
    text = "\n".join(lines)
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PostToolUse",
        "additionalContext": text,
    }}))
    sys.stdout.flush()
    print(text, file=sys.stderr)


def _shutdown_directive() -> str:
    """The instruction injected into a session that has been asked to quit.

    Save FIRST, mark ready SECOND — that ordering is the operator's own
    (documentation before exit) and the initiator enforces it too: a session
    with no readiness marker is not actuated unless --force is given.

    The marker carries this session's own pid and, where it can PROVE which
    pane is its own, its pane id. Neither is derivable from outside (there is
    no session->pid map and no session->pane map), so volunteering them here is
    what lets the initiator act without ever guessing a target.
    """
    sid = _resolve_session_id()
    # ABSOLUTE paths, and the session id THIS hook resolved.
    #
    # Both were defects caught by an adversarial audit on 2026-09-23, and they
    # compounded. The directive used to emit bare `bin/grip-py ...` and
    # `bin/grip-fleet-ready`, which resolve only from a checkout ROOT — of 1024
    # recorded session cwds, ~780 are neither the GRIP root nor a worktree root,
    # and `grip-fleet-ready` is not on PATH at all, so step 3 had no fallback.
    # A comment here even claimed cwd-independence while the line above it was a
    # relative path, and lib/fleet_quit.get_dir() — added for exactly this — had
    # zero callers.
    #
    # Passing --session explicitly closes the second half: called bare, the
    # script falls back to $CLAUDE_SESSION_ID, which need not equal the id this
    # hook resolved via mesh_identity. A marker written under a different id is
    # one the initiator never finds, so the session reports `timed-out` — which
    # this module's own docstring calls the conflation most likely to lose work.
    root = os.environ.get("CLAUDE_DIR") or str(Path.home() / ".claude")
    return (
        "[FLEET-QUIT] Another GRIP session has asked the fleet to close down.\n"
        "Do this now, in order:\n"
        "  1. Finish or safely abandon what you are doing; commit and push anything "
        "load-bearing (uncommitted work in a worktree is lost on exit).\n"
        f"  2. Run your close ritual:  {root}/bin/grip-py {root}/lib/grip_save.py\n"
        f"  3. Report ready:  {root}/bin/grip-fleet-ready --session {sid}\n"
        "  4. Then tell the operator you are ready to exit and stop working.\n"
        "If you have unpushed work you are NOT willing to lose, do step 1 properly "
        "before step 3 — nothing will force-close you until you report ready."
    )

def main() -> None:
    # Leave the parent's messages for the parent: see _from_background_agent.
    if _from_background_agent(_hook_payload()):
        return
    db_path = _resolve_db_path()
    if db_path is None:
        sys.exit(0)

    session_id = _resolve_session_id()
    rows = _fetch_pending(db_path, session_id)
    if not rows:
        return

    lines = [f"[MESH-INBOX from={r[1]} kind={r[3]}] {r[2]}" for r in rows]

    # First consumer of the `kind` column, which until now was rendered and
    # otherwise inert. A `shutdown` row is a fleet-quit request: the session
    # cannot terminate itself (lib/infinite_cycle.py) and no hook can force a
    # stop, so the only thing that works is telling the MODEL what to do, and
    # the model can run a Bash command even though it cannot invoke a slash
    # command. Hence `grip-py lib/grip_save.py` rather than `/save`.
    if any((r[3] or "") == "shutdown" for r in rows):
        lines.append(_shutdown_directive())

    _emit(lines)
    _mark_delivered(db_path, [r[0] for r in rows])


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
