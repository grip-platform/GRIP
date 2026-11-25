#!/usr/bin/env python3
"""ULTRACLEAR warm-reload — Claude Code SessionStart hook.

The READ half of auto-HOT-safe ``/clear``. A bare ``/clear`` is a harness
window-reset (NOT a compaction) — it drops the verbatim HOT working set. On the
fresh session start this hook finds the most-recent ULTRACLEAR checkpoint for
THIS project — written by a DIFFERENT, recent session (the throttled Stop-hook
writer) — and re-injects ONLY the small bounded HOT manifest, VERBATIM, into the
new window via SessionStart ``additionalContext``. It is a serialize reload, not
an LLM summary, so it is lossless; COLD turns page back on demand.

Cross-session safety (the exact failure class this feature exists to prevent —
a fresh session adopting a foreign/stale window): the checkpoint is selected by
project slug, EXCLUDING the current session id and anything older than the TTL.
``source == "compact"`` is skipped (compaction already preserved the context).

Contract mirrors hooks/ultraclear-checkpoint-stop.py:
  * CRASH-SAFE — the whole body is wrapped; ANY exception prints ``{}`` and never
    propagates. A SessionStart hook can never crash the session.
  * DETERMINISTIC — the wall-clock is INJECTED into ``process`` (``now_iso``);
    ``main`` supplies real UTC at runtime, tests pass a fixed instant.
  * Emission mirrors hooks/focus-cards-hook.py::_emit_session_start —
    ``suppressOutput`` + ``hookSpecificOutput.additionalContext`` (the model
    receives the HOT turns; the operator's startup box stays clean).
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# sys.path bootstrap — mirror the sibling hooks: CLAUDE_DIR (for `from lib.X`)
# and CLAUDE_DIR/lib (direct). Foundation modules are imported lazily INSIDE the
# wrapped body so an import failure still yields `{}`.
CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", str(Path.home() / ".claude")))
for _p in (str(CLAUDE_DIR), str(CLAUDE_DIR / "lib")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# /clear, startup, resume all benefit from a warm-reload; compaction already
# preserved the context in-window, so re-injecting would duplicate it.
_SKIP_SOURCES = {"compact"}
_TTL_SECONDS = 14_400          # 4h — an older window is not "this work".
_MANIFEST_TOKEN_CAP = 25_000   # bound the re-injected payload.

# L1 auto-continue directive — injected into the warm-reload manifest so a
# handoff (/ultraclear -> /clear) RESUMES on the operator's first go-signal
# instead of a re-summarise-and-ask round-trip. GATED on the first message being
# a bare go-signal, so a real instruction always wins and it cannot misfire on a
# casual /clear used to start new work. A SessionStart hook can only INJECT this
# (passive additionalContext); it cannot start a turn, so "continue"/Enter is the
# minimal trigger. rules/session-protocol.md's warm-start clause is the backstop.
_AUTO_CONTINUE_DIRECTIVE = (
    "[AUTO-CONTINUE ARMED] Warm-reload of recent working context. If the "
    'operator\'s FIRST message this session is a bare go-signal ("continue", '
    '"go", "next", "proceed", "resume", "y", or empty) AND a "## Next Step" '
    "block is present in this window, IMMEDIATELY act on it — do NOT re-summarise "
    "this manifest and do NOT ask what to do; execute that step. If NO "
    '"## Next Step" block is present, do NOT fabricate one — treat the go-signal '
    'as "what should we do next?" and ask. If the first message is a real '
    "instruction, follow it and treat this manifest as background context. For an "
    "AUTONOMOUS multi-step resume — work the \"## Next Step\" AND its remaining "
    "items to completion unattended — the operator types /afk instead of a bare "
    "go-signal; /afk arms autonomous mode (safe work only; the four HALT classes "
    "still stop and wait; TTL-bounded; /back to return), so treat a first message "
    "of /afk as the instruction to resume THIS warm-reload's work autonomously."
)


def _utc_now_iso() -> str:
    """Real wall-clock for runtime use (tests inject their own ``now`` instead)."""
    return datetime.now(timezone.utc).isoformat()


def _parse_stdin(stdin_text: str) -> Optional[dict]:
    """Parse the SessionStart payload, or None on empty/garbage/non-object input."""
    try:
        data = json.loads(stdin_text)
    except (ValueError, TypeError):
        return None
    return data if isinstance(data, dict) else None


def _turn_text(turn) -> str:
    """Verbatim text of a reloaded Turn (str content as-is; list -> text blocks)."""
    content = getattr(turn, "content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [b["text"] for b in content
                 if isinstance(b, dict) and isinstance(b.get("text"), str)]
        return "\n".join(parts)
    return str(content)


def _tokens_of(turn) -> int:
    """Token estimate of a Turn, 0 on a missing/corrupt field."""
    try:
        return int(getattr(turn, "tokens", 0))
    except (TypeError, ValueError):
        return 0


def _cap_recent(hot: list, token_cap: int):
    """Keep the most-recent turns whose cumulative tokens fit ``token_cap``, in
    chronological order. Returns (kept_turns, dropped_count). The single newest
    turn is always kept even if it alone exceeds the cap (never reload nothing)."""
    kept_rev: list = []
    total = 0
    for turn in reversed(hot):
        tok = _tokens_of(turn)
        if kept_rev and total + tok > token_cap:
            break
        kept_rev.append(turn)
        total += tok
    return list(reversed(kept_rev)), len(hot) - len(kept_rev)


def _render_manifest(hot: list, cp: dict) -> str:
    """additionalContext string: a provenance header + the verbatim HOT turns."""
    kept, dropped = _cap_recent(hot, _MANIFEST_TOKEN_CAP)
    prior = str(cp.get("session_id") or cp.get("session") or "a prior session")
    written = str(cp.get("written_at") or cp.get("created_at") or cp.get("now") or "")
    head = (f"[ULTRACLEAR HOT MANIFEST — {len(kept)} verbatim turn(s) reloaded from "
            f"{prior}{(' @ ' + written) if written else ''}. This is the load-bearing "
            f"working context a bare /clear dropped; COLD turns page back on demand.]")
    if dropped:
        head += (f"\n[+{dropped} older HOT turn(s) omitted to fit the "
                 f"{_MANIFEST_TOKEN_CAP}-token cap.]")
    head += "\n\n" + _AUTO_CONTINUE_DIRECTIVE
    body = "\n\n".join(f"— {getattr(t, 'role', '?')}:\n{_turn_text(t)}" for t in kept)
    return f"{head}\n\n{body}"


def _emit(context: str) -> dict:
    """SessionStart emission — mirror focus-cards-hook _emit_session_start: route
    the HOT context through additionalContext + suppressOutput (model sees it,
    terminal stays clean)."""
    return {
        "continue": True,
        "suppressOutput": True,
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": context,
        },
    }


def process(stdin_text: str, now_iso: str) -> dict:
    """Core coordinator (deterministic via injected ``now_iso``). Returns the hook
    payload dict; ``{}`` when there is nothing safe to reload. ``main`` is the
    ultimate crash net."""
    from ultraclear_capture import load_latest_checkpoint, project_slug  # lazy
    import ultraclear_session  # lazy
    data = _parse_stdin(stdin_text)
    if data is None:
        return {}
    if str(data.get("source") or "") in _SKIP_SOURCES:
        return {}
    cp = load_latest_checkpoint(
        project_slug(data.get("cwd")),
        exclude_session=str(data.get("session_id") or ""),
        ttl_seconds=_TTL_SECONDS,
        now_iso=now_iso,
    )
    if not cp:
        return {}
    hot = ultraclear_session.reload_manifest(cp)
    if not hot:
        return {}
    return _emit(_render_manifest(hot, cp))


def main() -> None:
    """SessionStart entry: read stdin, decide, ALWAYS print one JSON object, never
    raise (wrap the whole body — any exception -> ``{}``)."""
    try:
        payload = process(sys.stdin.read(), _utc_now_iso())
    except Exception:
        payload = {}
    print(json.dumps(payload))


if __name__ == "__main__":
    main()
