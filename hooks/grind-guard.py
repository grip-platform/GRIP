#!/usr/bin/env python3
"""Grind-guard PostToolUse hook — advises ONCE when a low-value test-fixing tail
is running past the point the core already shipped.

Advisory only: it emits a systemMessage and never blocks. Fail-open — any error
degrades to a silent allow. Detector: lib/grind_guard.py (pure). Audit:
logs/grind-guard.jsonl (one line per fire, with an outcome-label slot so the
benefit is measurable, per the council's RECALIBRATE verdict c586d543).

Registration: PostToolUse, matcher Bash|Edit|Write|MultiEdit. State is
per-session so concurrent sessions never share a streak.
"""
import json
import os
import sys
import time
from pathlib import Path

_HOOKS = Path(__file__).resolve().parent
_ROOT = _HOOKS.parent
for _p in (str(_HOOKS), str(_ROOT / "lib"), str(_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

try:
    from hook_utils import parse_hook_input, respond, extract_tool_info
    from grind_guard import update, fresh_state
except Exception:  # noqa: BLE001 — a broken import must not break the turn
    print("{}")
    sys.exit(0)

CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR_OVERRIDE")
                  or os.environ.get("CLAUDE_DIR")
                  or (Path.home() / ".claude"))
STATE_DIR = CLAUDE_DIR / "state" / "session"
LOG = CLAUDE_DIR / "logs" / "grind-guard.jsonl"


def _state_path(session_id: str) -> Path:
    safe = "".join(c for c in (session_id or "unknown") if c.isalnum() or c in "-_")[:64]
    return STATE_DIR / f"grind-guard-{safe or 'unknown'}.json"


def _load(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError, ValueError):
        return fresh_state()


def _save(path: Path, state: dict) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state))
    except OSError:
        pass


def _log_fire(session_id: str, fire: dict) -> None:
    """Append a fire with UNLABELLED outcome fields. The label is filled later
    (review / reaper): the guard retires if <2/20 fires precede a good ship."""
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with LOG.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({
                "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "session": session_id,
                "streak": fire["streak"],
                "distinct_targets": fire["distinct_targets"],
                "core_shipped": True,
                # outcome-label slot (Axis 4 — filled on review):
                "grind_confirmed": None,
                "shipped_and_handed_back_after": None,
                "was_right": None,
            }) + "\n")
    except OSError:
        pass


def main() -> None:
    payload = parse_hook_input()
    if not isinstance(payload, dict) or not payload:
        respond()
        return
    tool_name, tool_input, tool_response = extract_tool_info(payload)
    session_id = str(payload.get("session_id", "") or "unknown")

    path = _state_path(session_id)
    state = _load(path)
    try:
        new_state, fire = update(state, tool_name, tool_input, tool_response)
    except Exception:  # noqa: BLE001 — advisory must never raise into the turn
        respond()
        return
    _save(path, new_state)

    if fire:
        _log_fire(session_id, fire)
        respond(fire["message"])
    else:
        respond()


if __name__ == "__main__":
    try:
        main()
    except Exception:  # noqa: BLE001 — final fail-open backstop
        print("{}")
        sys.exit(0)
