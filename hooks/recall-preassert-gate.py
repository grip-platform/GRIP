#!/usr/bin/env python3
"""Stop hook (F2) — block negative-existence claims the moat contradicts.

Reads the Stop payload ({"session_id", "transcript_path", "hook_event_name",
"stop_hook_active", "last_assistant_message"}), takes the message the turn ends
on from the payload (the transcript only when the payload lacks it, because the
transcript can lag behind the event: lib/transcript_text.stop_message_text),
preasserts every negative-existence sentence, and on DENY blocks the stop with
the contradicting evidence (paths + hashes + IDR ids) so the agent surfaces
the contradiction to the operator as an ERROR STATE instead of re-asserting.
Every DENY also seals a RED-chain forensic node (severity=pre_output,
where_caught=output_gate) — hard-block + record, never silent.

Loop safety: honours ``stop_hook_active`` AND a seen-claim-hash file at
``<store>/gate-seen.json`` so the same claim is never blocked twice in a row.
Engine exceptions fail OPEN (allow + one line to logs/recall-gate.jsonl);
a COMPUTED DENY never fails open — emission errors cannot demote the block.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
for _p in (str(_ROOT), str(_ROOT / "lib")):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _allow() -> int:
    print("{}")
    return 0


def _log_fail_open(error: Exception) -> None:
    try:
        from lib.claude_dir import get_claude_dir
        log = get_claude_dir() / "logs" / "recall-gate.jsonl"
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({
                "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "fail_open": repr(error),
            }) + "\n")
    except Exception:  # noqa: BLE001 — logging must never raise
        pass


def _compute_denials(text: str) -> list:
    from lib.recall.preassert import negative_sentences, preassert
    from lib.recall.schema import VERDICT_DENY
    verdicts = [preassert(s) for s in negative_sentences(text)]
    return [v for v in verdicts if v.verdict == VERDICT_DENY]


def _seen_path() -> Path:
    from lib.recall.index import default_store
    return default_store() / "gate-seen.json"


def _already_blocked(denial_hashes: list) -> bool:
    """True iff every current denial was blocked on the immediately previous
    fire (never block the same claim twice in a row). Updates the file."""
    path = _seen_path()
    try:
        last = set(json.loads(path.read_text(encoding="utf-8")).get("last", []))
    except (OSError, json.JSONDecodeError, ValueError):
        last = set()
    repeat = bool(denial_hashes) and set(denial_hashes) <= last
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"last": [] if repeat else denial_hashes}),
                        encoding="utf-8")
    except OSError:
        pass
    return repeat


def _emit_forensics(denials: list, session_id: str) -> None:
    from lib.recall import forensic
    from lib.recall.schema import CAUGHT_OUTPUT_GATE, SEVERITY_PRE_OUTPUT
    for verdict in denials:
        node = forensic.build_node(
            verdict.claim, {"session_id": session_id, "harness": "claude-code"},
            evidence=verdict.evidence, severity=SEVERITY_PRE_OUTPUT,
            where_caught=CAUGHT_OUTPUT_GATE,
        )
        forensic.emit(node)


def _block_reason(denials: list) -> str:
    from lib.recall.schema import NEGATIVE_EXISTENCE_HINT
    lines = ["RECALL GATE: the moat contradicts a negative-existence claim "
             "in your last message. Evidence:"]
    for verdict in denials:
        lines.append(f'Claim: "{verdict.claim}"')
        lines.extend(
            f"  - {e.get('path')} (sha256={e.get('blob_hash') or '-'}, "
            f"idr={e.get('idr_id') or '-'})" for e in verdict.evidence[:5])
    lines.append(NEGATIVE_EXISTENCE_HINT)
    lines.append("Say plainly that your claim and the record disagree, as an "
                 "ERROR STATE (the record vs your claim) — do NOT re-assert the negative.")
    return "\n".join(lines)


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:  # noqa: BLE001 — malformed stdin: allow
        return _allow()
    if payload.get("stop_hook_active"):
        return _allow()
    try:
        from lib.transcript_text import stop_message_text
        text = stop_message_text(payload)
        if not text:
            return _allow()
        denials = _compute_denials(text)
    except Exception as exc:  # noqa: BLE001 — ENGINE failure: fail open, logged
        _log_fail_open(exc)
        return _allow()
    if not denials:
        return _allow()
    # A computed DENY never fails open from here on.
    from lib.recall.schema import content_hash
    try:
        if _already_blocked([content_hash(v.claim) for v in denials]):
            return _allow()
    except Exception:  # noqa: BLE001 — dedup defect: keep blocking (stop_hook_active bounds loops)
        pass
    try:
        _emit_forensics(denials, str(payload.get("session_id", "")))
    except Exception as exc:  # noqa: BLE001 — recording defect: still block
        _log_fail_open(exc)
    from lib.hook_response import emit
    emit({"decision": "block", "reason": _block_reason(denials)})
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # noqa: BLE001 — pre-compute defects allow; never crash a stop
        sys.exit(_allow())
