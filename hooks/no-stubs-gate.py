#!/usr/bin/env python3
"""No-Stubs / Completeness PreToolUse gate — the L2 enforcement of rules/no-stubs.md.

WARN by default (surfaces introduced scrubs to stderr + audits them, NEVER blocks).
DENY for INTRODUCED scrubs only when ``GRIP_NO_STUBS_ENFORCE=deny`` — the V>>-gated
promotion after the corpus test is green AND the WARN-window false-positive rate <10%.

Fail-open by construction: any parse/import/IO error exits 0 (allow). A quality gate must
never become an availability outage — a broken detector must not block writes (mirrors the
guardrail-evasion-gate posture). Self-exempt: the principle's own files.

Activation is the V>>-gated step (wire into settings.json PreToolUse Write|Edit). Until
wired, this file is inert — zero blast radius from merging it.
"""

import json
import os
import sys
from pathlib import Path

_CLAUDE = Path(os.environ.get("CLAUDE_DIR") or (Path.home() / ".claude"))
if str(_CLAUDE) not in sys.path:
    sys.path.insert(0, str(_CLAUDE))

try:
    from lib.no_stubs_detector import analyze_source
except Exception:  # noqa: BLE001 — fail-open: a broken import must never block a write
    sys.exit(0)

# The principle's own artefacts must be able to quote the forbidden shapes to document them.
_SELF_EXEMPT = ("no_stubs_detector", "no-stubs-gate", "no-stubs.md", "test_no_stubs")
_AUDIT = _CLAUDE / "logs" / "no-stubs-gate.jsonl"


def _scrub_names(content: str, path: str) -> set[str]:
    """Qualified names of 'scrub'-classified findings in content (empty on any error)."""
    try:
        return {f.qualified_name for f in analyze_source(content, path=path) if f.classification == "scrub"}
    except Exception:  # noqa: BLE001 — fail-open
        return set()


def _scrub_findings(content: str, path: str) -> list:
    try:
        return [f for f in analyze_source(content, path=path) if f.classification == "scrub"]
    except Exception:  # noqa: BLE001
        return []


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
    # Match the Edit tool's OWN semantics: replace_all -> every occurrence, else the
    # (unique) first. A bare replace(..., 1) diverges from a replace_all edit when
    # old_string occurs more than once -> phantom diff (AGORA #3445).
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
                    "path": path, "tool": tool, "qualified_name": f.qualified_name,
                    "signal": f.signal, "shape": f.shape,
                }) + "\n")
    except OSError as exc:
        # Never block on audit failure (fail-open), but surface the lost record so the
        # WARN-window FP calibration knows it is incomplete (AGORA #3445).
        print(f"[no-stubs-gate] audit write failed ({exc}); finding not recorded", file=sys.stderr)


def _emit(msg: str) -> None:
    if os.environ.get("GRIP_NO_STUBS_ENFORCE") == "deny":
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": msg,
        }}))
    else:
        print(msg, file=sys.stderr)  # WARN — non-blocking (Rule 15 signal)


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except Exception:  # noqa: BLE001 — fail-open
        sys.exit(0)
    tool = payload.get("tool_name", "")
    tool_input = payload.get("tool_input", {}) or {}
    path = tool_input.get("file_path", "")
    if tool not in ("Write", "Edit") or not path.endswith(".py") or any(s in path for s in _SELF_EXEMPT):
        sys.exit(0)

    after = _post_edit_content(path, tool, tool_input)
    if after is None:
        sys.exit(0)
    after_scrubs = {f.qualified_name: f for f in _scrub_findings(after, path)}
    if not after_scrubs:
        sys.exit(0)

    before_scrubs = _scrub_names(_current_content(path), path)
    introduced = [f for qn, f in after_scrubs.items() if qn not in before_scrubs]
    if not introduced:
        sys.exit(0)  # only PRE-EXISTING scrubs touched — don't block an unrelated edit

    _audit(path, tool, introduced)
    listing = "; ".join(f"{f.qualified_name} ({f.signal})" for f in introduced)
    _emit(
        f"[QUALITY: NO-STUBS] introduced scrub(s): {listing} — implement fully OR delete + "
        f"file a tracked issue (Completeness / No-Scrubs; rules/no-stubs.md). A stub emits a "
        f"false completeness signal that a later reader will build on and confabulate."
    )
    sys.exit(0)


if __name__ == "__main__":
    main()
