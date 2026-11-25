#!/usr/bin/env python3
"""pre-check-prompt — UserPromptSubmit gate.

The conversation-boundary complement to pre-check-hook.py (PreToolUse).
Scans V>>'s own input for the same linguistic patterns that commonly
trigger upstream content refusals, and denies with a rewrite hint before
the prompt reaches the API. By scrubbing trigger phrases at the entry
point, downstream assistant responses are less likely to echo them and
hit the upstream output filter.

Pattern store + scan helpers reused from pre-check-hook.py for DRY.
Single source of truth for rules: hooks/.pre-check-patterns.json.

Behaviour:
  - hit  -> {"decision":"block","reason":"..."} with rewrite hint
  - miss -> {} (silent allow)

Bypass:
  - PRECHECK_PROMPT_BYPASS=1 in shell env (for one-off operator overrides)
  - yolo marker (autonomous loops opt the gate out via lib/gates/yolo_mode)

The hook degrades to permit-all on any internal error so a broken gate
never obstructs work. Audit log shared with pre-check-hook.py at
state/pre-check.jsonl (entries tagged tool=UserPromptSubmit).

CLI helper modes (mirrors pre-check-hook.py):
  --simulate "text"  scan a prompt locally, print results, no log
  --stats            print audit-log summary (shared log with PreToolUse)

Origin: 2026-05-01 — V>> hit upstream 400s during the canonical happi.md
ultrado run because the conversation accumulated security-flavoured
vocabulary (falsification, attack, break, injection). The PreToolUse
gate prevented those phrases from reaching subagent dispatches but did
nothing for the conversation itself. This hook closes that gap one
layer up.
"""

import importlib.util
import json
import os
import sys
from pathlib import Path

CLAUDE_DIR = Path.home() / ".claude"
SIBLING_HOOK = CLAUDE_DIR / "hooks" / "pre-check-hook.py"
BYPASS_ENV_VAR = "PRECHECK_PROMPT_BYPASS"


def _load_sibling():
    """Load pre-check-hook.py as a module and return it.

    Lazy-loaded so this hook stays useful even if the sibling is absent
    (in which case we degrade to permit-all).
    """
    if not SIBLING_HOOK.exists():
        return None
    try:
        spec = importlib.util.spec_from_file_location("pre_check", SIBLING_HOOK)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    except Exception:
        return None


def _yolo_bypass_active():
    """True if the operator has activated yolo mode for this gate."""
    try:
        sys.path.insert(0, str(CLAUDE_DIR / "lib"))
        from gates.yolo_mode import is_yolo_bypass
    except ImportError:
        return False
    try:
        return bool(is_yolo_bypass("pre-check-prompt"))
    except Exception:
        return False


def _bypass_active():
    """Two independent bypass paths — env var, or yolo marker."""
    if os.environ.get(BYPASS_ENV_VAR, "").strip():
        return True
    return _yolo_bypass_active()


def _allow():
    """No-op response — empty object, allow the prompt through."""
    print(json.dumps({}))


def _deny(reason):
    """Block the prompt and surface the rewrite hint."""
    print(json.dumps({"decision": "block", "reason": reason}))


def _extract_message(payload):
    """Read the user message from the payload across the schema variants."""
    for key in ("message", "content", "user_message", "prompt"):
        val = payload.get(key)
        if isinstance(val, str) and val:
            return val
    return ""


def _hook_dispatch():
    """Default mode: read UserPromptSubmit JSON from stdin and decide."""
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError, OSError):
        _allow()
        return 0

    message = _extract_message(payload)
    if not message:
        _allow()
        return 0

    if _bypass_active():
        _allow()
        return 0

    sibling = _load_sibling()
    if sibling is None:
        # Degrade to permit-all if the sibling pattern store is unavailable.
        _allow()
        return 0

    try:
        hits = sibling._scan(message)
    except Exception:
        _allow()
        return 0

    if not hits:
        _allow()
        return 0

    try:
        sibling._log("UserPromptSubmit", hits, message)
    except Exception:
        pass

    try:
        reason = sibling._format_reason(hits)
    except Exception:
        reason = "[pre-check] Prompt would likely be refused upstream. Rewrite before resubmit."

    _deny(reason)
    return 0


def _simulate(text):
    sibling = _load_sibling()
    if sibling is None:
        print("pre-check-prompt simulate: sibling pre-check-hook.py not found; degraded to allow")
        return 0
    return sibling._simulate(text)


def _stats():
    sibling = _load_sibling()
    if sibling is None:
        print("pre-check-prompt stats: sibling pre-check-hook.py not found")
        return 0
    return sibling._stats()


def main():
    if len(sys.argv) > 1:
        mode = sys.argv[1]
        if mode == "--simulate":
            return _simulate(" ".join(sys.argv[2:]))
        if mode == "--stats":
            return _stats()
        if mode in ("-h", "--help"):
            print(
                "Usage:\n"
                "  (stdin JSON)              UserPromptSubmit hook dispatch\n"
                "  --simulate <text...>      scan a prompt locally, print results, no log\n"
                "  --stats                   summarise the shared audit log (PreToolUse + UserPromptSubmit)\n"
                "  --help                    show this message"
            )
            return 0
    return _hook_dispatch()


if __name__ == "__main__":
    sys.exit(main())
