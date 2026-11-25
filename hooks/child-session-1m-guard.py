#!/usr/bin/env python3
"""SessionStart hook — surface the 1M-context fix on child/forked sessions (#514).

Why this exists (3rd recurrence of the 200K-thrash class, 2026-07-17):
GRIP's launch path (`bin/grip` grip_handoff) injects
`--model claude-opus-5-5` so a FRESH session boots the 1,000,000-token
window. (The pin was `claude-opus-4-8[1m]` when this hook was written and is
re-read from bin/grip's GRIP_1M_MODEL, not hardcoded here — Opus 5.5 is
natively 1M and carries no `[1m]` suffix, so a docstring naming the old
variant taught the wrong shape to the next reader.) A CHILD / forked / resumed session (Claude Code sets
CLAUDE_CODE_CHILD_SESSION=1) does NOT re-run that launch path — it inherits
the PARENT session's model (verified 2026-07-17 via an empirical probe, #518:
the parent's model, NOT the configured default — so a durable default is
IGNORED by child sessions; the lineage root must pin `[1m]`), which may be a
non-`[1m]` variant. Claude Code's window resolver then returns the 200K base window
(ff(), docs/grip-tier-1m-context.md) and auto-compact thrashes at 200K against
GRIP's large preload. Ref: memory reference_grip_1m_model_variant_boot_fix.

A SessionStart hook CANNOT repin a live window — Claude Code fixes the window
at process launch and no hook output changes it. So this guard does the
achievable, honest thing: on a child session that is NOT confirmably on the 1M
window, it surfaces the exact one-command fix, converting a SILENT 200K thrash
into a VISIBLE one-command correction.

Scope discipline (near-zero noise):
  - No-op unless CLAUDE_CODE_CHILD_SESSION=1 (the exact bypass class; every
    normal fresh/gg++ launch never sees this hook fire).
  - Best-effort suppressor: if GRIP's model-state EXPLICITLY confirms the
    window is >= 1M, stay silent (the child already has 1M — nothing to fix).
    Unknown / absent state falls through to surface (fail-toward-visible — the
    cost of missing a real 200K child outweighs a redundant one-line note).
  - Once per session (marker keyed on the session id) — never repeats.

Fail-safe: any error exits 0 silently. SessionStart hooks MUST never block
bootstrap. Reversibility: removing this hook is a clean no-op.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

_ONE_MILLION = 1_000_000
# The picker label for GRIP's launch default (bin/grip GRIP_1M_MODEL). It named
# Opus 4.8 until 2026-09-24, two generations after the default had moved on.
_FIX = "/model → Opus 5.5 (1M context)"
MESSAGE = (
    "[GRIP #514] Child/forked session detected. GRIP's launch-time 1M-context "
    "pin does NOT carry into child sessions (they inherit the PARENT session's "
    "model, which can be a non-1M variant → the 200K window + auto-compact "
    "thrash). If the statusline shows /200K instead of /1M, run:  " + _FIX +
    "  to restore the full 1M window. (A hook cannot repin a live window; the "
    "durable fix is for the launch/lineage root to pin [1m].)"
)


def should_surface(child_flag, ctx_window) -> bool:
    """Fire iff this IS a child session AND the window is not confirmably 1M.

    child_flag : the CLAUDE_CODE_CHILD_SESSION env value ("1" on a child).
    ctx_window : the model-state context_window int, or None when unknown.

    Truth table (pinned by tests/test_child_session_1m_guard.py):
      not a child                 -> False (no-op — the 99% path)
      child + window >= 1M        -> False (already 1M — nothing to fix)
      child + window <= 200K      -> True  (the bug — surface the fix)
      child + window unknown/None -> True  (fail-toward-visible)
    """
    if str(child_flag or "") != "1":
        return False
    if isinstance(ctx_window, int) and ctx_window >= _ONE_MILLION:
        return False
    return True


def _model_state_window(lib_dir: Path):
    """Best-effort read of GRIP's model-state context_window; None on any miss."""
    try:
        if str(lib_dir) not in sys.path:
            sys.path.insert(0, str(lib_dir))
        from state_manager import load_state  # noqa: PLC0415 — runtime boundary

        state = load_state("model-state") or {}
        cw = state.get("context_window")
        return cw if isinstance(cw, int) else None
    except Exception:  # noqa: BLE001 — fail toward "unknown" (surface)
        return None


def _session_id() -> str:
    """Session id from the SessionStart stdin payload, else the env, else ''."""
    sid = os.environ.get("CLAUDE_CODE_SESSION_ID", "")
    try:
        raw = sys.stdin.read()
        if raw.strip():
            sid = json.loads(raw).get("session_id", sid) or sid
    except Exception:  # noqa: BLE001
        pass
    return str(sid or "")


def _marker(claude_dir: Path, sid: str) -> Path:
    name = "child-1m-guard-%s.json" % (sid or "unknown")
    return claude_dir / "state" / "session" / name


def main() -> int:
    repo = Path(__file__).resolve().parent.parent  # ~/.claude/
    lib = repo / "lib"
    claude_dir = Path(os.environ.get("CLAUDE_DIR", str(repo)))

    if not should_surface(
        os.environ.get("CLAUDE_CODE_CHILD_SESSION"), _model_state_window(lib)
    ):
        return 0

    sid = _session_id()
    marker = _marker(claude_dir, sid)
    if sid and marker.exists():
        return 0  # already surfaced this session
    print(MESSAGE)
    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text("{}\n")
    except OSError:
        pass  # best-effort — worst case it surfaces again next start
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # noqa: BLE001 — never block bootstrap
        sys.exit(0)
