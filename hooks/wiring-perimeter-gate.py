#!/usr/bin/env python3
"""Wiring-Perimeter PreToolUse gate — L2 enforcement of
rules/declared-input-must-reach-the-actor.md.

WARN by default (surfaces INTRODUCED declared-but-unread inputs to stderr +
audits them, NEVER blocks). DENY for introduced findings only when
``GRIP_WIRING_PERIMETER_ENFORCE=deny`` — the V>>-gated promotion after a WARN
window confirms the measured false-positive rate stays under the no-stubs.md
10% threshold (calibration 2026-08-17: 0 findings across 1028 lib/ files
post-tuning; the 4 mandatory git-object cases all detected).

Fail-open by construction: any parse/import/IO error exits 0 (allow). A quality
gate must never become an availability outage. Self-exempt: the principle's own
files (they must be able to quote the defect shapes to document them).

Activation is the V>>-gated step (a binding in config/hooks.registry.json →
PreToolUse Write|Edit). Until wired, this file is inert — zero blast radius.
"""

import json
import os
import sys
from pathlib import Path

_CLAUDE = Path(os.environ.get("CLAUDE_DIR") or (Path.home() / ".claude"))
if str(_CLAUDE) not in sys.path:
    sys.path.insert(0, str(_CLAUDE))

try:
    from lib.wiring_perimeter_detector import analyze_source
except Exception:  # noqa: BLE001 — fail-open: a broken import must never block a write
    sys.exit(0)

_SELF_EXEMPT = ("wiring_perimeter_detector", "wiring-perimeter-gate",
                "test_wiring_perimeter", "declared-input-must-reach-the-actor")
_AUDIT = _CLAUDE / "logs" / "wiring-perimeter-gate.jsonl"


def _finding_keys(content: str, path: str) -> dict[tuple[str, str], object]:
    """{(shape, name): Finding} for content (empty on any error)."""
    try:
        return {(f.shape, f.name): f
                for f in analyze_source(content, path=path, repo_root=_CLAUDE)}
    except Exception:  # noqa: BLE001 — fail-open
        return {}


def _post_edit_content(path: str, tool: str, tool_input: dict):
    """The full file content AFTER the proposed tool call (None if not derivable)."""
    if tool == "Write":
        return tool_input.get("content", "")
    try:
        current = Path(path).read_text(encoding="utf-8")
    except OSError:
        return None
    old, new = tool_input.get("old_string", ""), tool_input.get("new_string", "")
    if not old or old not in current:
        return None
    # Match the Edit tool's OWN semantics (mirrors no-stubs-gate, AGORA #3445).
    return current.replace(old, new) if tool_input.get("replace_all") else current.replace(old, new, 1)


def _current_content(path: str) -> str:
    try:
        return Path(path).read_text(encoding="utf-8")
    except OSError:
        return ""


def _audit(path: str, tool: str, introduced: list) -> None:
    try:
        _AUDIT.parent.mkdir(parents=True, exist_ok=True)
        with open(_AUDIT, "a", encoding="utf-8") as fh:
            for f in introduced:
                fh.write(json.dumps({
                    "path": path, "tool": tool, "shape": f.shape,
                    "name": f.name, "line": f.line,
                }) + "\n")
    except OSError as exc:
        print(f"[wiring-perimeter-gate] audit write failed ({exc}); "
              f"finding not recorded", file=sys.stderr)


def _emit(msg: str) -> None:
    if os.environ.get("GRIP_WIRING_PERIMETER_ENFORCE") == "deny":
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": msg,
        }}))
    else:
        print(msg, file=sys.stderr)  # WARN — non-blocking


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except Exception:  # noqa: BLE001 — fail-open
        sys.exit(0)
    tool = payload.get("tool_name", "")
    tool_input = payload.get("tool_input", {}) or {}
    path = tool_input.get("file_path", "")
    if (tool not in ("Write", "Edit") or not path.endswith(".py")
            or any(s in path for s in _SELF_EXEMPT)):
        sys.exit(0)

    after = _post_edit_content(path, tool, tool_input)
    if after is None:
        sys.exit(0)
    after_findings = _finding_keys(after, path)
    if not after_findings:
        sys.exit(0)

    before_keys = set(_finding_keys(_current_content(path), path))
    introduced = [f for key, f in after_findings.items() if key not in before_keys]
    if not introduced:
        sys.exit(0)  # only PRE-EXISTING findings touched — never block an unrelated edit

    _audit(path, tool, introduced)
    listing = "; ".join(f"{f.name} [{f.shape}] :{f.line}" for f in introduced)
    _emit(
        f"[QUALITY: WIRING-PERIMETER] declared input reaches nothing: {listing} — "
        "wire the input to its actor OR remove the declaration "
        "(rules/declared-input-must-reach-the-actor.md). An input that is accepted "
        "and silently dropped makes the artefact look like it answered the caller."
    )
    sys.exit(0)


if __name__ == "__main__":
    main()
