#!/usr/bin/env python3
"""Congruent Continuity push RECORDER hook — record-only, PreToolUse-shaped.

Contract (mirrors ``hooks/continuity-watch-hook.py``): read one JSON payload
``{"tool_name", "tool_input", "session_id", "transcript_path", "cwd"}`` from
stdin; when the tool is Bash and the command contains ``git push``, call
``lib.continuity_recorder.record_push_premise`` so the level-with-main
premise is on the signed chain BEFORE the push runs. The hook ALWAYS allows:
exit code 0 on every path, nothing on stdout (allow by omission), nothing on
stderr (the signing layer's own notices are captured into the audit row).
It records; it never gates, steers or blocks — that is what keeps it
inside build-order decision D3 and clears the escape-hatch audit
(``rules/gate-resolution-escape-hatch.md`` Invariant 1) by construction.

Fail-open by construction: any internal error (garbage stdin, broken import,
unreadable chain, git unavailable) is appended to
``logs/continuity-recorder.jsonl`` and the tool call proceeds. The same log
records every premise written, so the recorder's own activity is auditable.

The chain is session-namespaced through ``CLAUDE_SESSION_ID``
(``lib/context_head.py::_session_path``); when the environment lacks it the
payload's ``session_id`` is exported first, so a hook process and the
session it serves write the same chain file.

Env (each read below — declared-input-must-reach-the-actor):
- ``GRIP_CONTINUITY_RECORDER``      ``off`` disables recording entirely
- ``GRIP_CONTINUITY_CHAIN``         chain file override (tests)
- ``GRIP_CONTINUITY_HEAD``          HEAD pointer override (tests)
- ``GRIP_CONTINUITY_RECORDER_LOG``  audit/error log path
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _log_path() -> Path:
    override = os.environ.get("GRIP_CONTINUITY_RECORDER_LOG", "").strip()
    if override:
        return Path(override)
    base = Path(os.environ.get("CLAUDE_DIR", Path.home() / ".claude"))
    return base / "logs" / "continuity-recorder.jsonl"


def _audit(row: dict) -> None:
    try:
        path = _log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), **row}) + "\n")
    except OSError:
        pass


def main() -> int:
    if os.environ.get("GRIP_CONTINUITY_RECORDER", "").strip().lower() == "off":
        return 0
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except (json.JSONDecodeError, OSError):
        return 0
    if not isinstance(payload, dict) or payload.get("tool_name") != "Bash":
        return 0
    tool_input = payload.get("tool_input")
    command = tool_input.get("command") if isinstance(tool_input, dict) else None
    if not isinstance(command, str) or "git push" not in command:
        return 0
    session_id = payload.get("session_id") if isinstance(payload.get("session_id"), str) else None
    if session_id and not os.environ.get("CLAUDE_SESSION_ID", "").strip():
        os.environ["CLAUDE_SESSION_ID"] = session_id
    try:
        from lib.continuity_recorder import record_push_premise

        chain = os.environ.get("GRIP_CONTINUITY_CHAIN", "").strip()
        head = os.environ.get("GRIP_CONTINUITY_HEAD", "").strip()
        # The chain's signing layer may print a custody notice on stderr; a
        # PreToolUse hook's stderr reaches the operator's terminal, so it is
        # captured here and kept in the audit row instead of shown per push.
        captured = io.StringIO()
        with contextlib.redirect_stderr(captured):
            node = record_push_premise(
                command=command,
                cwd=payload.get("cwd") or os.getcwd(),
                session_id=session_id,
                transcript_path=payload.get("transcript_path") if isinstance(payload.get("transcript_path"), str) else None,
                path=Path(chain) if chain else None,
                head_pointer=Path(head) if head else None,
            )
        notes = captured.getvalue().strip()[:300]
        row = {"action": "skipped" if node is None else "recorded", "session_id": session_id}
        if node is not None:
            row.update({"id": node.id, "claim": node.decision.get("claim")})
        if notes:
            row["notes"] = notes
        _audit(row)
    except Exception as exc:  # noqa: BLE001 — fail-open is the contract
        _audit({"action": "error", "error": repr(exc)[:300], "session_id": session_id})
    return 0


if __name__ == "__main__":
    # Dual-sign custody guard (GRIP#5628). This entrypoint has a
    # `#!/usr/bin/env python3` shebang and reaches a signed-record write, so under
    # an interpreter without `cryptography>=48` it would silently emit forgeable
    # `demo-shared-key` records. Re-exec under a capable interpreter FIRST, before
    # main() reaches any signing path.
    import sys as _sys
    from pathlib import Path as _Path
    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent / "lib"))
    from pq_reexec import reexec_if_incapable as _reexec
    _reexec(_Path(__file__))

    sys.exit(main())
