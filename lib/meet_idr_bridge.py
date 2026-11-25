"""Meet → IDR bridge: live-meeting decisions become signed, ratifiable records.

The "Talk to your business" wire. The live-meeting loop (scripts/intel_act_loop.py,
launched by bin/grip-meet) classifies each transcript chunk and prints its
verdicts to state/meet/actloop.log — `complex` verdicts land as standalone JSON
lines {topic, action, addressed, response, reason}. This bridge:

  1. parse_meeting_events(path)   — pull decision-shaped rows out of that log
                                    (or any JSONL of decisions), tolerating torn
                                    lines and prose noise; never raises on bad rows.
  2. emit_idrs(decisions, emit_fn) — one signed PrecogIDR per decision via
                                    lib.precog.idr (why/what/how captured at the
                                    moment of decision); emit_fn injected (DIP).
  3. pending_ratifications(path)  — cards for the morning brief: every
                                    meeting-decision IDR with no matching
                                    meeting-ratification IDR yet, with a
                                    "reply: yes <id>" tap hint.
  4. record_ratification(idr_id)  — append the ratification record that makes a
                                    card disappear (the "yes <id>" handler calls
                                    this).

ADDITIVE by design: reads the act-loop's output, never touches the intel
pipeline files. Falsifier: if parsed decisions ever drop the why/intent line,
or a torn line crashes the parse, tests/test_meet_idr_bridge.py fails.

CLI:
    python3 -m lib.meet_idr_bridge --meeting <path> [--dry-run]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Callable, Optional

KIND_DECISION = "meeting-decision"
KIND_RATIFICATION = "meeting-ratification"
STATUS_PENDING = "pending-ratification"

# emit_fn contract: (kind, decision_payload) -> emitted IDR id.
EmitFn = Callable[[str, dict], str]


def _fingerprint(payload: dict) -> str:
    """Deterministic content coordinate over the semantic decision body."""
    body = {k: payload.get(k) for k in ("what", "why", "how")}
    blob = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(blob.encode()).hexdigest()


def _from_verdict_row(row: dict) -> Optional[dict]:
    """Act-loop verdict JSON → decision dict. Only `complex` verdicts are
    business decisions; none/acknowledge/answer/stop are not."""
    if row.get("action") != "complex" or not str(row.get("topic", "")).strip():
        return None
    return {
        "when": row.get("ts") or row.get("when"),
        "speaker": row.get("speaker"),
        "decision": str(row["topic"]).strip(),
        "why": str(row.get("reason", "")).strip(),
        "how": str(row.get("response") or row.get("action", "")).strip(),
    }


def _from_decision_row(row: dict) -> Optional[dict]:
    """Generic decision row {decision, why, how, speaker?, when?} → decision dict."""
    if not str(row.get("decision", "")).strip():
        return None
    return {
        "when": row.get("when") or row.get("ts"),
        "speaker": row.get("speaker"),
        "decision": str(row["decision"]).strip(),
        "why": str(row.get("why") or row.get("reason", "")).strip(),
        "how": str(row.get("how") or row.get("action", "")).strip(),
    }


def _coerce_row(line: str) -> Optional[dict]:
    """One stripped line → decision dict, or None for noise.

    Verdict shape (the live-meeting writer's native format) is tried first;
    the generic decision shape is the forward-compatible JSONL contract.
    Torn JSON and non-dict rows coerce to None — never raise.
    """
    if not line.startswith("{"):
        return None
    try:
        row = json.loads(line)
    except json.JSONDecodeError:
        return None  # torn line — partial write mid-append
    if not isinstance(row, dict):
        return None
    return _from_verdict_row(row) or _from_decision_row(row)


def parse_meeting_events(path: Path | str) -> list[dict]:
    """Parse decision dicts out of a meeting event log / JSONL.

    Tolerant by construction: prose lines, torn (truncated) JSON, and
    non-decision verdicts are skipped silently. Never raises on bad rows;
    a missing file yields []. O(n) over lines.
    """
    target = Path(path)
    if not target.exists():
        return []
    lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
    coerced = (_coerce_row(line.strip()) for line in lines)
    return [decision for decision in coerced if decision is not None]


def default_emit(kind: str, payload: dict) -> str:
    """Production emit: build + HMAC-sign + append one IDR to the precog ledger.

    Returns the new IDR id. Imported lazily so parse-only and test paths never
    touch the signing key.
    """
    from lib.precog.idr import append_idr, build_idr

    idr = build_idr(
        prompt=str(payload.get("what") or payload.get("ratifies", "")),
        fingerprint=_fingerprint(payload),
        decision=payload,
        predecessor_idr=None,
        depth=0,
        kind=kind,
        inputs={"source": "meet-idr-bridge"},
    )
    append_idr(idr)
    return idr.id


def emit_idrs(decisions: list[dict], emit_fn: EmitFn = default_emit) -> list[str]:
    """One signed IDR per decision. The why (intent line) and how (action) are
    captured in the signed body — the decision's mind at the moment it was made.

    Returns the emitted IDR ids in input order.
    """
    ids: list[str] = []
    for d in decisions:
        payload = {
            "what": d.get("decision", ""),
            "why": d.get("why", ""),
            "how": d.get("how", ""),
            "speaker": d.get("speaker"),
            "when": d.get("when"),
            "status": STATUS_PENDING,
        }
        ids.append(emit_fn(KIND_DECISION, payload))
    return ids


def record_ratification(idr_id: str, emit_fn: EmitFn = default_emit) -> str:
    """Append the ratification record for one meeting decision.

    The morning-brief card says "reply: yes <id>" — the handler acting on that
    reply calls this. Returns the ratification IDR's id.
    """
    return emit_fn(KIND_RATIFICATION, {"ratifies": idr_id})


def pending_ratifications(path: Path | None = None) -> list[dict]:
    """Cards for meeting decisions awaiting ratification.

    Reads the precog IDR ledger (default state/precog/idr.jsonl via
    lib.precog.idr.read_idr_chain). A decision is pending until a
    meeting-ratification record naming its id appears.
    """
    from lib.precog.idr import read_idr_chain

    records = read_idr_chain(path=path)
    ratified = {
        r.decision.get("ratifies")
        for r in records
        if r.kind == KIND_RATIFICATION
    }
    return [
        {
            "id": r.id,
            "title": r.decision.get("what", ""),
            "why": r.decision.get("why", ""),
            "ratify_hint": f"reply: yes {r.id}",
        }
        for r in records
        if r.kind == KIND_DECISION and r.id not in ratified
    ]


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Turn live-meeting decisions into signed, ratifiable IDRs")
    parser.add_argument("--meeting", required=True,
                        help="path to the meeting event log (e.g. state/meet/actloop.log)")
    parser.add_argument("--dry-run", action="store_true",
                        help="show what would be recorded, sign nothing")
    args = parser.parse_args(argv)

    decisions = parse_meeting_events(args.meeting)
    if not decisions:
        print("No meeting decisions found — nothing to record.")
        return 0
    print(f"Found {len(decisions)} decision(s) in {args.meeting}:")
    for d in decisions:
        print(f"  - {d['decision']} (why: {d['why'] or 'not stated'})")
    if args.dry_run:
        print("Dry run — no records signed.")
        return 0
    ids = emit_idrs(decisions)
    print(f"Signed {len(ids)} decision record(s). They will appear in the "
          f"morning brief for ratification — reply 'yes <id>' to ratify.")
    for idr_id in ids:
        print(f"  {idr_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
