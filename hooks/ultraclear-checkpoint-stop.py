#!/usr/bin/env python3
"""ULTRACLEAR auto-checkpoint — Claude Code Stop hook.

Fires after every assistant response. It cheaply serialises the live session's
HOT manifest to ``state/ultraclear/<session_id>.json`` (+ a per-project pointer)
so a later ``/clear`` warm-reloads ONLY the small HOT set instead of paying to
re-read the whole window. Writes are THROTTLED — at most one checkpoint per
``state/ultraclear/.throttle-<slug>.json`` baseline window — so a Stop on every
turn does not re-serialise on every turn.

Decision (delegated VERBATIM to the lib/ultraclear_capture foundation):
  build_turns_from_transcript(transcript_path) -> (turns, kernel_tokens)
  should_checkpoint(last_iso, last_turns, last_tokens, cur_turns, cur_tokens, now)
  write_checkpoint(turns, kernel_tokens, session_id, now_iso) -> Path | None
A MISSING throttle file means "first run" -> force a checkpoint.

Contract (mirrors hooks/focus-cards-hook.py exactly):
  * CRASH-SAFE — the whole body is wrapped; ANY exception (bad stdin, missing
    foundation, corrupt throttle) prints ``{}`` and never propagates. A hook can
    never crash the session.
  * DETERMINISTIC — the wall-clock is INJECTED into ``process`` (``now_iso``);
    ``main`` supplies real UTC at runtime, tests pass a fixed instant.
  * ATOMIC writes — checkpoint persistence is the foundation's unique-tmp +
    os.replace; the throttle reuses the same crash-safe writer (no duplication).
  * <1s budget — pure stdlib + the stdlib-only ultraclear* / claude_dir shims.

Output: ``{"continue": true, "suppressOutput": true}`` when a checkpoint is
written (quiet), ``{}`` on a throttled no-op or any failure.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# sys.path bootstrap — mirror focus-cards-hook.py: add CLAUDE_DIR (for `from
# lib.X`) and CLAUDE_DIR/lib (for direct `from X`). The foundation modules are
# imported lazily INSIDE the wrapped body so an import failure still yields `{}`.
CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", str(Path.home() / ".claude")))
for _p in (str(CLAUDE_DIR), str(CLAUDE_DIR / "lib")):
    if _p not in sys.path:
        sys.path.insert(0, _p)


# --------------------------------------------------------------------------
# tiny pure helpers
# --------------------------------------------------------------------------

def _utc_now_iso() -> str:
    """Real wall-clock for runtime use (tests inject their own ``now`` instead)."""
    return datetime.now(timezone.utc).isoformat()


def _parse_stdin(stdin_text: str) -> Optional[dict]:
    """Parse the hook payload, or None on empty/garbage/non-object input."""
    try:
        data = json.loads(stdin_text)
    except (ValueError, TypeError):
        return None
    return data if isinstance(data, dict) else None


def _as_int(value) -> int:
    """Coerce a possibly-missing/corrupt throttle field to int (0 on failure)."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _count_tokens(turns) -> int:
    """Cumulative token estimate across the live turns (the throttle's token arm)."""
    return sum(_as_int(getattr(t, "tokens", 0)) for t in turns)


# --------------------------------------------------------------------------
# throttle state (rate-limit baseline) — reuses the foundation's crash-safe IO
# --------------------------------------------------------------------------

def _throttle_file(slug: str) -> Path:
    """``<claude_dir>/state/ultraclear/.throttle-<slug>.json`` — resolved fresh
    so a runtime CLAUDE_DIR override (tests) is honoured."""
    from claude_dir import get_claude_dir
    return get_claude_dir() / "state" / "ultraclear" / f".throttle-{slug}.json"


def _read_throttle(slug: str) -> Optional[dict]:
    """Last baseline ``{last_iso, turns, tokens}``, or None when absent/corrupt
    (None == first run -> force a checkpoint)."""
    from ultraclear_capture import _read_json
    return _read_json(_throttle_file(slug))


def _write_throttle(slug: str, now_iso: str, turns: int, tokens: int) -> None:
    """Advance the baseline atomically. Never raises (a lost throttle write only
    means the next Stop re-checkpoints — harmless)."""
    from ultraclear_capture import _atomic_write_json
    try:
        _atomic_write_json(
            _throttle_file(slug),
            {"last_iso": now_iso, "turns": turns, "tokens": tokens},
        )
    except Exception:
        pass


# --------------------------------------------------------------------------
# decide + act
# --------------------------------------------------------------------------

def _is_due(state, cur_turns: int, cur_tokens: int, now_iso: str,
            should_checkpoint) -> bool:
    """True when a fresh checkpoint is due. A MISSING throttle (state is None) is
    a first run -> force True; otherwise delegate to the foundation's thresholds."""
    if state is None:
        return True
    return should_checkpoint(
        str(state.get("last_iso") or ""),
        _as_int(state.get("turns")),
        _as_int(state.get("tokens")),
        cur_turns,
        cur_tokens,
        now_iso,
    )


def _do_checkpoint(turns, kernel_tokens, session_id: str, slug: str,
                   cur_turns: int, cur_tokens: int, now_iso: str,
                   write_checkpoint) -> bool:
    """Write the checkpoint; advance the throttle baseline ONLY if one landed.
    Returns True iff a checkpoint was persisted."""
    cp_path = write_checkpoint(turns, kernel_tokens, session_id, now_iso, slug=slug)
    if cp_path is None:
        return False
    _write_throttle(slug, now_iso, cur_turns, cur_tokens)
    return True


def _reap_dead_checkpoints(reap_stale_checkpoints, now_iso: str) -> None:
    """Opportunistic dead-checkpoint GC — fired ONLY after a checkpoint write landed,
    so it is self-throttled to the checkpoint cadence (not every Stop). The reaper is
    itself fail-safe; this extra guard means a reap error can never touch the hook's
    return path (rules/lifecycle-closed-or-expired.md: an autonomous, self-throttled
    trigger so the TTL is actually enforced without an operator or a separate cron)."""
    try:
        reap_stale_checkpoints(now_iso)
    except Exception:
        pass


def process(stdin_text: str, now_iso: str) -> dict:
    """Core coordinator (deterministic via injected ``now_iso``). Returns the hook
    payload dict. Internally fail-soft; ``main`` is the ultimate crash net."""
    from ultraclear_capture import (  # lazy: any import failure -> main prints {}
        build_turns_from_transcript,
        project_slug,
        reap_stale_checkpoints,
        should_checkpoint,
        write_checkpoint,
    )
    data = _parse_stdin(stdin_text)
    if data is None:
        return {}
    session_id = str(data.get("session_id") or "")
    if not session_id:
        return {}
    slug = project_slug(data.get("cwd"))
    turns, kernel_tokens = build_turns_from_transcript(data.get("transcript_path") or "")
    cur_turns, cur_tokens = len(turns), _count_tokens(turns)
    state = _read_throttle(slug)
    if not _is_due(state, cur_turns, cur_tokens, now_iso, should_checkpoint):
        return {}
    written = _do_checkpoint(turns, kernel_tokens, session_id, slug,
                             cur_turns, cur_tokens, now_iso, write_checkpoint)
    if written:
        _reap_dead_checkpoints(reap_stale_checkpoints, now_iso)
    return {"continue": True, "suppressOutput": True} if written else {}


def main() -> None:
    """Stop-hook entry: read stdin, decide, ALWAYS print one JSON object, never
    raise (wrap the whole body — any exception -> ``{}``)."""
    try:
        payload = process(sys.stdin.read(), _utc_now_iso())
    except Exception:
        payload = {}
    print(json.dumps(payload))


if __name__ == "__main__":
    main()
