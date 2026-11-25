#!/usr/bin/env python3
"""Stop-hook advisory: tag unverified self-reports before anyone acts on them.

Fires at turn end when the payload carries the final assistant text. When
that text asserts something about the agent's OWN reasoning, observations,
or behaviour ("I checked X and it's not there", "I never ran Y") with no
adjacent evidence (command block, exit code, file:line, repro label), it
injects an advisory reminder listing the spans. It never blocks the stop and
fails open on any internal error.

Detector: lib/self_report_tagger.py (pure, zero side effects)
Tests:    tests/test_self_report_tagger.py
Audit:    logs/self-report-tagger.jsonl (one line per tagged turn)
Origin:   2026-07-14 (hypothesis H798) — self-reports about own behaviour
          were externally contradicted 4/4 times in one session; the fix is
          a ground-truth test, never a more elaborate self-account.

Activation is the operator-gated step (wire into settings.json Stop). Until
wired, this file is inert — zero blast radius from merging. Payloads without
a final-assistant-text field are silently allowed (this surface degrades to
a no-op rather than guessing from transcripts).
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
    from lib.self_report_tagger import classify, quotable_sources
except Exception:  # noqa: BLE001 — fail-open: a broken import must never block a stop
    sys.exit(0)

try:  # L1.5 semantic tier (SIGD discriminative classifier) — optional by design
    from lib.confab_sigd_classifier import consult as _semantic_consult
except Exception:  # noqa: BLE001 — the regex floor alone is a valid gate
    _semantic_consult = None

try:  # transcript fallback — the CC Stop payload carries no assistant text key
    from lib.transcript_text import last_assistant_text, recent_tool_calls
except Exception:  # noqa: BLE001 — degrade to payload-keys-only if unavailable
    last_assistant_text = None
    recent_tool_calls = None

_AUDIT = _CLAUDE / "logs" / "self-report-tagger.jsonl"

# Payload keys that may carry the final assistant text, across harness shapes.
_TEXT_KEYS = ("last_assistant_message", "assistant_message", "final_message", "message")

_WARN_TEMPLATE = (
    "[UNVERIFIED SELF-REPORT — ground-truth-test before acting] the last "
    "response asserts about its own behaviour without evidence: {spans}"
)


def _audit(record: dict) -> None:
    """Append one JSONL audit record; audit loss never blocks anything."""
    try:
        _AUDIT.parent.mkdir(parents=True, exist_ok=True)
        record["ts"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        with _AUDIT.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")
    except OSError as exc:
        print(f"[self-report-tagger] audit write failed ({exc})", file=sys.stderr)


def _emit_tag(spans: list[str]) -> None:
    """Record the advisory. VISIBLE emission is opt-in, and off by default.

    A Stop hook's ``additionalContext`` is rendered by the harness AFTER the
    assistant's response, so anything emitted here is structurally guaranteed to
    be the LAST thing on the operator's screen. That put a raw internal
    diagnostic below every report, which is the exact failure
    ``rules/canonical-output-style.md`` exists to prevent: the report must end
    the response, and this pushed it above machinery the operator never asked to
    read. Reported by V>> on 2026-08-23 with a screenshot of the effect.

    The signal is not lost -- every fire is still audited to
    ``logs/self-report-tagger.jsonl``, which is where it can be measured
    without costing the operator a single line. Set
    ``GRIP_SELF_REPORT_VISIBLE=1`` to restore inline emission, matching the
    opt-in shape ``GRIP_PROVE_FOOTER`` already uses for the same reason.

    Silence here is a display decision, never a detection one: the tagger still
    runs, still classifies and still records.
    """
    if os.environ.get("GRIP_SELF_REPORT_VISIBLE", "").strip() not in {"1", "true", "yes"}:
        return
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "Stop",
        "additionalContext": _WARN_TEMPLATE.format(spans="; ".join(spans[:5])),
    }}))


def _final_text(payload: dict) -> str:
    """The final assistant text for this turn.

    Some harness shapes hand it to the hook under a payload key; the Claude Code
    Stop payload does NOT — it carries only ``transcript_path``, so without the
    transcript fallback this hook never sees the turn it advises on (its audit
    log was empty until 2026-07-17). Try the direct keys first (cheap,
    cross-harness), then read the last assistant turn from the transcript.
    """
    for key in _TEXT_KEYS:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value
    if last_assistant_text is not None:
        return last_assistant_text(payload.get("transcript_path"))
    return ""


def _quoted_sources(payload: dict) -> tuple[str, ...]:
    """Same-turn tool output admissible as quoted evidence (issue #4409).

    Quoting a curl/gh/cat result is the behaviour the rules want, so it must not
    read as an unverified self-report. Output the agent authored itself (echo,
    printf, a heredoc) is excluded by ``quotable_sources`` — otherwise a claim
    could be laundered by printing it first and quoting it back.

    Fails open to ``()``, which restores the exact pre-#4409 behaviour.
    """
    if recent_tool_calls is None:
        return ()
    try:
        return quotable_sources(recent_tool_calls(payload.get("transcript_path")))
    except Exception:  # noqa: BLE001 — evidence lookup must never break the hook
        return ()


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:  # noqa: BLE001 — fail-open on malformed stdin
        return 0
    text = _final_text(payload)
    if not text:
        return 0

    verdict = classify(text, semantic_consult=_semantic_consult,
                       quoted_sources=_quoted_sources(payload))
    if verdict["verdict"] != "tag":
        return 0
    spans = [f'"{r["span"]}" ({r["kind"]})' for r in verdict["self_reports"]]
    _audit({"spans": spans, "n": len(spans)})
    _emit_tag(spans)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # noqa: BLE001 — fail-open: never block a stop on a hook defect
        sys.exit(0)
