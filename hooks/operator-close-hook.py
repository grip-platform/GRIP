#!/usr/bin/env python3
"""Stop-hook advisory: catch responses that tell the operator to stop, or close for him.

Fires at turn end. When the final assistant text tells the operator to sleep or
rest, infers his state from the clock, or declares the session over on his behalf,
it injects one advisory line naming the spans. It never blocks the stop and fails
open on any internal error.

Detector: lib/operator_close_detector.py (pure, zero side effects)
Tests:    tests/test_operator_close_detector.py
Audit:    logs/operator-close-hook.jsonl (one line per tagged turn)
Rule:     rules/never-close-on-the-operators-behalf.md

Origin: 2026-09-02. The rule and its memory already existed and were violated
SIXTEEN times in one evening without the author noticing. Per
feedback_grip_forgets_by_not_binding_not_by_not_learning.md that is a Class B
failure — recurrence despite a written record — for which writing a better record
is the fix already tried and already failed. Hence a shim.

Self-exemption is LINE-SCOPED, not turn-scoped: a line naming this feature is
dropped before scanning, and every other line is still scanned. Skipping the whole
turn on a name-mention would let any response evade by citing the module. The
residual gap is a line that both names the feature and closes on the operator's
behalf, which goes unseen — accepted, because the alternative fires on every
rule-authoring turn and trains the advisory to be ignored.

The corpus itself is rarely at risk: a Stop payload carries the assistant's PROSE,
not its tool inputs, so writing the phrasings into a file does not put them here.
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
    from lib.operator_close_detector import scan_full, advisory
except Exception:  # noqa: BLE001 — a broken import must never block a stop
    sys.exit(0)

try:
    from lib.transcript_text import last_assistant_text
except Exception:  # noqa: BLE001
    last_assistant_text = None

_AUDIT = _CLAUDE / "logs" / "operator-close-hook.jsonl"
_TEXT_KEYS = ("last_assistant_message", "assistant_message", "final_message", "message")
_SELF_EXEMPT = ("operator_close_detector", "operator-close-hook",
                "never-close-on-the-operators-behalf", "never-tell-operator-to-sleep")


def _text(payload: dict) -> str:
    for k in _TEXT_KEYS:
        v = payload.get(k)
        if isinstance(v, str) and v.strip():
            return v
    tp = payload.get("transcript_path")
    if last_assistant_text and isinstance(tp, str) and tp:
        try:
            return last_assistant_text(tp) or ""
        except Exception:  # noqa: BLE001
            return ""
    return ""


def _may_send(payload: dict) -> bool:
    """True only when this session works inside a GRIP checkout. Fails closed.

    The classifier is an outside model. A session anywhere else (the private
    guardian repository, a legal folder, a client project) keeps its turns on
    this machine, and the lexical floor runs alone. lib/grip_checkout.py.
    """
    cwd = payload.get("cwd")
    if not isinstance(cwd, str) or not cwd:
        return False
    try:
        from lib.grip_checkout import grip_checkout_root
        return grip_checkout_root(cwd) is not None
    except Exception:  # noqa: BLE001 -- a fault here keeps the turn on this machine
        return False


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:  # noqa: BLE001
        return 0
    if not isinstance(payload, dict):
        return 0

    text = _text(payload)
    if not text:
        return 0
    scanned = "\n".join(
        ln for ln in text.splitlines()
        if not any(tok in ln for tok in _SELF_EXEMPT)
    )
    # Lexical floor + a cheap outside model reading what the text MEANS: 5/5 on
    # phrasings no pattern covers, 8/8 on third-party cases, ~900ms cold, free on
    # a cache hit. The floor still fires with no model, and runs alone for a
    # session outside a GRIP checkout, whose turns never leave this machine.
    findings, source = scan_full(scanned, ask_model=_may_send(payload))
    if not findings:
        return 0

    try:
        _AUDIT.parent.mkdir(parents=True, exist_ok=True)
        with _AUDIT.open("a") as fh:
            fh.write(json.dumps({
                "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "session": payload.get("session_id"),
                "kinds": sorted({f.kind for f in findings}),
                "semantic_source": source,
                # WHY the classified text is stored, not just the span: on
                # 2026-09-03 three fires could not be adjudicated after the
                # fact. The span is the last line; the classifier judges the
                # WHOLE turn, so the span alone cannot reproduce the verdict.
                # Without this the rule's falsification clause — "count entries
                # with a non-empty kinds field" — measures a number nobody can
                # check. This is the assistant's own output, not the operator's.
                # The tail is stored whenever the head does not already hold
                # the whole turn. The first cut stored it only past 800 chars,
                # so a 600-char turn kept its first 400 and lost its ending --
                # the part that usually carries the close. AGORA on GRIP#5377.
                "classified_len": len(scanned),
                "classified_head": scanned[:400],
                "classified_tail": scanned[-400:] if len(scanned) > 400 else "",
                "spans": [f.span for f in findings][:8],
            }) + "\n")
    except Exception:  # noqa: BLE001 — audit failure must not block
        pass

    note = advisory(findings)
    # Claude Code's TUI SUPPRESSES Stop-hook stderr — documented in
    # hooks/insight-propagation-scanner.py::write_pending_artefact. A stderr-only
    # advisory is therefore audited and never read, which is the shipped-inert
    # defect this whole shim exists to avoid. The supported envelope is
    # hookSpecificOutput.additionalContext on STDOUT, which reaches the next turn.
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "Stop",
        "additionalContext": note,
    }}))
    print(note, file=sys.stderr)   # for non-TUI harnesses, which do show it
    return 0


if __name__ == "__main__":
    sys.exit(main())
