#!/usr/bin/env python3
"""SessionStart hook — stamp operator's active Claude Code session id.

Writes ``$CLAUDE_SESSION_ID`` + wall-clock to
``~/.claude/state/relay/operator-session-id.txt`` so the VPS relay
dispatcher can later spawn ``claude -p --resume <id> --fork-session`` and
fork the operator's foreground conversation history into its reply.

The actual write is delegated to ``lib/relay_operator_session.write_stamp``
(KISS — one place owns the atomic-write contract). This hook is the
*caller* boundary; the library is the *capability* boundary.

Fail-safe: any error (missing env var, unwritable dir, import failure)
exits 0 silently. SessionStart hooks MUST never block session bootstrap.

Reversibility:
  * Without the env stamp file, ``read_active_session_id`` returns None.
  * Without the dispatcher's ``GRIP_RELAY_OPERATOR_SESSION_ROUTING=1`` flag,
    ``build_argv`` never consults the file.
  Removing this hook from settings.json is a clean no-op.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def _resolve_session_id(payload: dict | None = None) -> str | None:
    """Resolve the operator's active session id, most-authoritative first.

    Priority:
      1. the SessionStart hook stdin payload's ``session_id`` — the canonical
         value Claude Code passes to hooks (same field
         hooks/context-gate-hook.py and hooks/auto-memory-hook.py read);
      2. the payload's ``transcript_path`` stem (``…/<session_id>.jsonl``) —
         a belt-and-braces derivation when ``session_id`` is absent;
      3. the ``CLAUDE_SESSION_ID`` env var — the original path, kept for
         back-compat, but NOT populated in every launch mode (the 2026-07-12
         stamp-absent bug this resolver fixes).

    First non-empty source wins; None ⇒ nothing to stamp.
    """
    payload = payload or {}
    sid = str(payload.get("session_id") or "").strip()
    if sid:
        return sid
    transcript = str(payload.get("transcript_path") or "").strip()
    if transcript:
        stem = Path(transcript).stem  # <session_id>.jsonl → <session_id>
        if stem:
            return stem
    env_sid = os.environ.get("CLAUDE_SESSION_ID", "").strip()
    return env_sid or None


def _read_stdin_nonblocking(timeout: float = 0.1) -> str:
    """Read the piped payload WITHOUT hanging (honour the never-block contract).

    A SessionStart hook must never block session bootstrap, so we never do an
    unbounded ``sys.stdin.read()``:
      * tty stdin (no piped payload) → "" immediately — the classic infinite
        hang, avoided;
      * a real piped fd → wait up to ``timeout`` for data. The harness writes
        the payload and closes the pipe, so a ready read reaches EOF promptly;
        an empty/non-delivering pipe times out to "".
      * no real fd (e.g. a StringIO in tests) → a plain read, since there is
        nothing to ``select`` on.
    """
    stdin = sys.stdin
    try:
        if stdin.isatty():
            return ""
        fd = stdin.fileno()
    except (OSError, ValueError, AttributeError):
        return stdin.read()  # StringIO / no real fd → read directly
    import select  # lazy — keeps hook startup cheap
    ready, _, _ = select.select([fd], [], [], timeout)
    if not ready:
        return ""
    # Strictly bounded: os.read on a select-ready fd returns the available bytes
    # WITHOUT waiting for EOF, so this cannot hang even if the writer never
    # closes the pipe (unlike stdin.read(), which reads to EOF). A SessionStart
    # payload is small — one read gets it; a larger/chunked write degrades to a
    # partial read → JSON parse fails → {} → env fallback, still fail-safe.
    return os.read(fd, 65536).decode("utf-8", errors="replace")


def _read_payload() -> dict:
    """Best-effort parse of the SessionStart hook's stdin JSON payload.

    Fail-safe: an empty/absent/malformed/tty payload yields ``{}`` — the resolver
    then falls through to the env var. Never blocks *unboundedly* — the stdin
    read is bounded by ``_read_stdin_nonblocking``'s short (~0.1s) timeout. A
    residual read/select error still fails safe to ``{}`` (and ``main`` carries
    the outer catch-all guard for anything narrower doesn't cover).
    """
    try:
        raw = _read_stdin_nonblocking()
    except (OSError, ValueError):
        return {}
    if not raw.strip():
        return {}
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def main() -> int:
    payload = _read_payload()
    sid = _resolve_session_id(payload)
    if not sid:
        return 0

    # Project dir the session runs in — session ids are PROJECT-SCOPED, so
    # the dispatcher must fork-spawn from this cwd for --resume <sid> to
    # resolve (2026-07-13 live miss: a worktree session's stamp was
    # unresolvable from the dispatcher's default cwd). Payload cwd is the
    # harness-authoritative value; os.getcwd() is the fallback (hooks run in
    # the session's cwd).
    cwd = str(payload.get("cwd") or "").strip() or os.getcwd()

    # Late import — keeps hook startup cheap and isolates failures.
    repo = Path(__file__).resolve().parent.parent  # ~/.claude/
    lib = repo / "lib"
    if str(lib) not in sys.path:
        sys.path.insert(0, str(lib))
    try:
        import relay_operator_session  # noqa: WPS433  (lazy by design)
        relay_operator_session.write_stamp(sid, cwd=cwd)
    except Exception:  # noqa: BLE001  fail-safe: hook must never block
        return 0
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # noqa: BLE001
        sys.exit(0)
