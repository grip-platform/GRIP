#!/usr/bin/env python3
"""Stop-hook advisory: flag bare internal reference numbers before the operator
reads them. Mirrors hooks/self-report-tagger.py (same fail-open contract).

When the final assistant text carries an internal issue / PR / task number or a
hypothesis id with NO adjacent plain-language gloss, inject an advisory listing
the offending tokens for the NEXT turn. A Stop hook CANNOT rewrite already-
emitted prose, so this is advisory only — it never blocks the stop, and fails
open on any internal error.

Detector: lib/bare_number_detector.py (pure, zero side effects)
Tests:    tests/test_bare_number_detector.py
Audit:    logs/bare-number-advisory.jsonl (one line per tagged turn)
Origin:   2026-07-15 (harness task #438) — prose-only enforcement of the
          canonical-output-style gloss clause decays late-session. Operator
          directive: make references EXOGENOUS (link the real GitHub issue),
          never a bare internal number the operator or another harness/LLM
          cannot resolve.

Activation is operator-gated (wired into settings.json Stop alongside
self-report-tagger.py). Payloads without a final-assistant-text field are
silently allowed (degrades to a no-op rather than guessing from transcripts).
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

_CLAUDE = Path(os.environ.get("CLAUDE_DIR") or (Path.home() / ".claude"))
if str(_CLAUDE) not in sys.path:
    sys.path.insert(0, str(_CLAUDE))

try:
    from lib.bare_number_detector import detect_bare_numbers
except Exception:  # noqa: BLE001 — fail-open: a broken import must never block a stop
    sys.exit(0)

try:  # transcript fallback — the CC Stop payload carries no assistant text key
    from lib.transcript_text import last_assistant_text
except Exception:  # noqa: BLE001 — degrade to payload-keys-only if unavailable
    last_assistant_text = None

_AUDIT = _CLAUDE / "logs" / "bare-number-advisory.jsonl"
_TEXT_KEYS = ("last_assistant_message", "assistant_message", "final_message", "message")
_WARN_TEMPLATE = (
    "[BARE-NUMBER — add a plain-language gloss, or link the exogenous GitHub "
    "issue] the last response uses internal reference numbers with no gloss "
    "(the operator cannot resolve a bare #N, and harness numbers collide with "
    "GitHub numbers): {spans}"
)


def _audit(record: dict) -> None:
    """Append one JSONL audit record; audit loss never blocks anything."""
    try:
        _AUDIT.parent.mkdir(parents=True, exist_ok=True)
        record["ts"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        with _AUDIT.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")
    except OSError as exc:
        print(f"[bare-number-advisory] audit write failed ({exc})", file=sys.stderr)


def _final_text(payload: dict) -> str:
    """The final assistant text for this turn.

    Some harness shapes hand it to the hook under a payload key; the Claude Code
    Stop payload does NOT — it carries only ``transcript_path``, so without the
    transcript fallback this hook never sees the turn it advises on (its audit
    log was empty from #4044 until 2026-07-17). Try the direct keys first (cheap,
    cross-harness), then read the last assistant turn from the transcript.
    """
    for key in _TEXT_KEYS:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value
    if last_assistant_text is not None:
        return last_assistant_text(payload.get("transcript_path"))
    return ""


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:  # noqa: BLE001 — fail-open on malformed stdin
        return 0
    text = _final_text(payload)
    if not text:
        return 0

    verdict = detect_bare_numbers(text)
    if verdict["verdict"] != "tag":
        return 0
    spans = [f'"{f["token"]}" in: {f["line"]}' for f in verdict["findings"]]
    _audit({"spans": spans, "n": len(spans)})
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "Stop",
        "additionalContext": _WARN_TEMPLATE.format(
            spans="; ".join(s[:120] for s in spans[:5])
        ),
    }}))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # noqa: BLE001 — fail-open: never block a stop on a hook defect
        sys.exit(0)
