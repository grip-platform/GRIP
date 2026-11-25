#!/usr/bin/env python3
"""
grip-browser Result Shield -- PostToolUse Hook
==============================================
The second layer under GRIP's grip-browser attachment. The PreToolUse gate
(hooks/grip-browser-tool-policy-gate.py) decides which tools may run; this
hook decides what the model SEES of a result that did run.

Why: a rendered page carries whatever the site put there, and that includes
other people's names, addresses, phone numbers and email addresses. Those
belong to the task, never to the transcript (``rules/no-sensitive-info-in-grip.md``,
``rules/personal-matters-stay-private.md``, and the standing instruction that
every outside-model egress is shielded by default). An allowed observe tool
such as ``get_page_content`` is exactly where that text arrives.

How: every ``mcp__grip-browser__*`` tool result is passed through
``lib/redaction_shield.py``'s ``Shield`` in the ``llm_egress`` profile (the
pinned denylist plus the generic PII floor, with pinned pseudonyms) and the
redacted text is returned as PostToolUse ``updatedMCPToolOutput``. Claude Code
2.1.301 applies that field to MCP tools; the probe that established it is
``docs/measurements/2026-10-18-claude-code-hook-rewrite-mcp.md``. A result the
shield leaves unchanged produces no output at all, so the harness delivers
the original.

Fail CLOSED for content: when the shield cannot run (the denylist is missing,
locked by git-crypt or invalid; the module import fails; the payload is not
the PostToolUse shape the matcher routes here), the result is REPLACED by a
sentence that names the fault and the fix. A redactor that crashes open would
hand the model exactly the text it exists to withhold. The fix -- repair the
denylist or Edit this hook -- lies outside the call it withholds
(rules/gate-resolution-escape-hatch.md, Invariant 1), and the gate above still
denies the credential, code-exec and server-admin classes, so nothing here
widens the surface.

Known limit: the pseudonym session lives for one hook run, so the same person
in two results gets two pseudonyms. Consistent pseudonyms across a run need a
persisted session, which is a later decision.

Every run appends one line to logs/grip-browser-result-shield.jsonl (counts
per category, never the text); the log write is fail-open.
"""

import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

PREFIX = "mcp__grip-browser__"
AUDIT_LOG_ENV = "GRIP_BROWSER_SHIELD_AUDIT_LOG"
DENYLIST_ENV = "GRIP_REDACTION_DENYLIST"
_DEFAULT_AUDIT_LOG = Path(__file__).resolve().parent.parent / "logs" / "grip-browser-result-shield.jsonl"

WITHHELD = (
    "[GRIP] grip-browser result withheld: the result shield could not run ({why}). "
    "The page was rendered, but its text is not shown until lib/redaction_shield.py "
    "can load its denylist (check `grip-py lib/redaction_shield.py --selftest` and "
    "the git-crypt state) or hooks/grip-browser-result-shield.py is repaired; "
    "Edit is not gated here."
)


def response_text(tool_response) -> str:
    """The result as the model would see it: a string as is, anything else as JSON."""
    if tool_response is None:
        return ""
    if isinstance(tool_response, str):
        return tool_response
    try:
        return json.dumps(tool_response, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(tool_response)


def shield_text(text: str) -> tuple:
    """(redacted text, counts by category). Raises when the shield cannot run."""
    from lib.redaction_shield import PROFILE_LLM_EGRESS, Shield, load_denylist
    override = (os.environ.get(DENYLIST_ENV) or "").strip()
    denylist = load_denylist(Path(override)) if override else load_denylist()
    result = Shield(denylist, profile=PROFILE_LLM_EGRESS).redact(text)
    return result.text, dict(result.counts)


def evaluate(tool_name, tool_response) -> tuple:
    """(replacement output or None, audit record). None means: deliver the
    original result unchanged."""
    if not isinstance(tool_name, str) or not tool_name.startswith(PREFIX):
        return None, None
    text = response_text(tool_response)
    if not text:
        return None, {"tool": tool_name[len(PREFIX):], "outcome": "empty"}
    try:
        redacted, counts = shield_text(text)
    except Exception as exc:  # noqa: BLE001 -- any fault withholds the content
        why = f"{type(exc).__name__}: {str(exc)[:160]}"
        return WITHHELD.format(why=why), {"tool": tool_name[len(PREFIX):],
                                          "outcome": "withheld", "why": why}
    if redacted == text:
        return None, {"tool": tool_name[len(PREFIX):], "outcome": "clean", "chars": len(text)}
    return redacted, {"tool": tool_name[len(PREFIX):], "outcome": "redacted",
                      "chars": len(text), "counts": counts}


def _audit(record: dict) -> None:
    if not record:
        return
    try:
        override = (os.environ.get(AUDIT_LOG_ENV) or "").strip()
        log = Path(override) if override else _DEFAULT_AUDIT_LOG
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(dict(record, ts=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())),
                                ensure_ascii=False) + "\n")
    except OSError:
        pass


def output(replacement: str) -> dict:
    return {"hookSpecificOutput": {"hookEventName": "PostToolUse",
                                   "updatedMCPToolOutput": replacement}}


def _read_event():
    try:
        event = json.load(sys.stdin)
    except (json.JSONDecodeError, EOFError, ValueError):
        return None
    return event if isinstance(event, dict) else None


def main():
    event = _read_event()
    if event is None:
        print(json.dumps(output(WITHHELD.format(why="unreadable PostToolUse payload"))))
        _audit({"tool": "?", "outcome": "withheld", "why": "unreadable payload"})
        sys.exit(0)
    replacement, record = evaluate(event.get("tool_name"), event.get("tool_response"))
    _audit(record)
    if replacement is not None:
        print(json.dumps(output(replacement), ensure_ascii=False))


if __name__ == "__main__":
    main()
