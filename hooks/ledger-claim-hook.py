#!/usr/bin/env python3
"""ledger-claim-hook -- PostToolUse: claim each file this session writes or edits.

Clean Ledger Pillar 1.5. Pillar 1 (bin/grip-git, bin/grip-stash,
bin/grip-ownership) attributes only work routed through its own tools, so a
file an agent wrote with Write or Edit -- most of the dirt in the shared tree
-- read UNATTRIBUTED. This hook closes that gap for Claude Code sessions: after
every Write, Edit, MultiEdit or NotebookEdit it claims the touched file for the
session (lib/grip_dirty_snapshot.py claim_paths), or releases the session's
claim when the edit left the file clean.

SHIPPED UNBOUND. It adds a one-file `git status` and, when something changed,
one small manifest write to every edit, so switching it on is the operator's
decision. To bind it, add to the PostToolUse block of settings.json (and to
config/hooks.registry.json, which regenerates that wiring):

    matcher: "Write|Edit|MultiEdit|NotebookEdit"
    command: bash "${CLAUDE_DIR:-$HOME/.claude}/lib/grip-python.sh" \\
             "${CLAUDE_DIR:-$HOME/.claude}/hooks/ledger-claim-hook.py"
    timeout: 5

Contract: fail-open. It always exits 0 and prints `{}`; an edit never fails
because the ledger did. Anything that stops it attributing a file -- no
session id, a git or ledger error, its own 4 s deadline, a SIGTERM from the
harness -- appends one line to logs/ledger-claim-hook.jsonl under the GRIP dir
(gitignored runtime data), and nothing else. The deadline and the SIGTERM
handler both raise inside the claim, so the manifest lock's finally-block
always runs; a hook killed while holding the lock would otherwise leave it
for the 10 s stale break, and other sessions' claims would be dropped.

Limits, stated rather than hidden: a file_path whose case differs from the
file on disk (possible on a case-insensitive filesystem) is not matched, so
that edit stays UNATTRIBUTED; edits inside a submodule are ledgered by the
submodule's own repository; and a formatter hook racing this one can leave
the session's own file OWNED-DRIFTED until its next edit re-claims it.
"""
from __future__ import annotations

import json
import os
import signal
import sys
import time
from pathlib import Path
from typing import Optional

_HOOKS = Path(__file__).resolve().parent
for _dir in (_HOOKS, _HOOKS.parent / "lib"):
    if str(_dir) not in sys.path:
        sys.path.insert(0, str(_dir))

import grip_dirty_snapshot as ledger  # noqa: E402
from claude_dir import get_claude_dir  # noqa: E402
from hook_utils import parse_hook_input, respond  # noqa: E402

#: Wall-clock bound on one claim; below the recommended 5 s binding timeout.
DEADLINE_S = 4.0
# Tool name -> the tool_input key that names the file it touched.
PATH_KEYS = {"Write": "file_path", "Edit": "file_path", "MultiEdit": "file_path",
             "NotebookEdit": "notebook_path"}


def target_paths(hook_input: dict) -> list[str]:
    """The file a supported tool touched, made absolute against the hook's cwd."""
    key = PATH_KEYS.get(str(hook_input.get("tool_name", "")))
    tool_input = hook_input.get("tool_input")
    if key is None or not isinstance(tool_input, dict):
        return []
    path = tool_input.get(key)
    if not isinstance(path, str) or not path.strip():
        return []
    cwd = hook_input.get("cwd")
    return [os.path.join(cwd if isinstance(cwd, str) and cwd else os.getcwd(), path)]


def session_of(hook_input: dict) -> Optional[str]:
    """This session's id, in the same order bin/grip-git resolves it.

    An explicit GRIP_SESSION_ID wins, as it does for grip-git, grip-stash and
    grip-ownership, so an override never splits one session across two
    manifests. Next the payload's session_id, which Claude Code also exports
    to Bash as CLAUDE_CODE_SESSION_ID (the same value: checked 2026-09-29
    against hooks/continuity-push-recorder.py's payload log), then the env.
    A value that fails the allowlist is refused, never replaced: it becomes a
    filename, and substituting another id would misattribute the edit.
    """
    explicit = (os.environ.get("GRIP_SESSION_ID") or "").strip()
    given = explicit or str(hook_input.get("session_id") or "").strip()
    if given:
        return given if ledger.valid_session_id(given) else None
    try:
        return ledger.resolve_session_id()
    except ledger.InvalidSessionId:
        return None


def audit(event: dict) -> None:
    """One JSON line per unattributed edit or failure. Never raises."""
    try:
        log = get_claude_dir() / "logs" / "ledger-claim-hook.jsonl"
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"ts": time.time(), **event}) + "\n")
    except OSError:
        return


class HookInterrupted(Exception):
    """The deadline passed, or the harness sent SIGTERM, mid-claim."""


def _interrupt(signum: int, _frame: object) -> None:
    raise HookInterrupted(signal.Signals(signum).name)


def deadline_seconds() -> float:
    """DEADLINE_S, or a positive GRIP_LEDGER_HOOK_DEADLINE_S (tests shorten it)."""
    try:
        value = float(os.environ.get("GRIP_LEDGER_HOOK_DEADLINE_S", DEADLINE_S))
    except ValueError:
        return DEADLINE_S
    return value if value > 0 else DEADLINE_S


def arm_deadline(seconds: float) -> None:
    """Turn a stall or a harness kill into an exception inside the claim.

    POSIX gets a real-time timer; Windows has none, so there git's own
    per-call timeout is the bound.
    """
    signal.signal(signal.SIGTERM, _interrupt)
    if hasattr(signal, "setitimer"):
        signal.signal(signal.SIGALRM, _interrupt)
        signal.setitimer(signal.ITIMER_REAL, seconds)


def disarm_deadline() -> None:
    if hasattr(signal, "setitimer"):
        signal.setitimer(signal.ITIMER_REAL, 0)


def claim(hook_input: dict, paths: list[str]) -> None:
    tool = str(hook_input.get("tool_name", ""))
    sid = session_of(hook_input)
    if sid is None:
        audit({"skip": "no valid session id", "tool": tool, "paths": paths})
        return
    try:
        ledger.claim_paths(paths, sid, op=tool)
    except Exception as exc:  # noqa: BLE001 -- fail-open by contract: an edit never fails because the ledger did
        disarm_deadline()
        audit({"error": f"{type(exc).__name__}: {exc}", "tool": tool,
               "paths": paths, "session_id": sid})


def main() -> int:
    hook_input = parse_hook_input()
    if isinstance(hook_input, dict):
        paths = target_paths(hook_input)
        if paths:
            arm_deadline(deadline_seconds())
            try:
                claim(hook_input, paths)
            except HookInterrupted:
                pass  # interrupted while auditing an earlier failure: nothing more to record
            finally:
                disarm_deadline()
    respond()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
