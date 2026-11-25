"""HAL/HAPPI adapter — the harness-agnostic syscall wrapper (F3).

``filter_output(text)`` scans outbound text for negative-existence claims,
preasserts each against the moat, and on DENY prepends an evidence
annotation block + seals a forensic node (where_caught=syscall_wrapper).
Imports only ``lib.recall.*`` + ``lib.claude_dir`` (transitively), so any
harness that can call Python can mediate its output through this one seam.
"""

from __future__ import annotations

from lib.recall import forensic
from lib.recall.preassert import negative_sentences, preassert
from lib.recall.schema import (
    CAUGHT_SYSCALL_WRAPPER,
    NEGATIVE_EXISTENCE_HINT,
    SEVERITY_PRE_OUTPUT,
    VERDICT_DENY,
)


def _evidence_lines(evidence: tuple) -> list[str]:
    lines = []
    for e in evidence[:5]:
        idr = f" idr={e.get('idr_id')}" if e.get("idr_id") else ""
        sha = f" sha256={e.get('blob_hash') or e.get('hash') or ''}".rstrip("= ")
        lines.append(f"  - {e.get('path', '')}{sha}{idr}")
    return lines


def _annotation(denials: list) -> str:
    lines = ["[RECALL GATE — the moat contradicts a negative claim in this output]"]
    for verdict in denials:
        lines.append(f'Claim: "{verdict.claim}"')
        lines.extend(_evidence_lines(verdict.evidence))
    lines.append(NEGATIVE_EXISTENCE_HINT)
    return "\n".join(lines)


def _seal(verdict, identity, store) -> str | None:
    node = forensic.build_node(
        verdict.claim, identity,
        evidence=verdict.evidence,
        severity=SEVERITY_PRE_OUTPUT,
        where_caught=CAUGHT_SYSCALL_WRAPPER,
        store=store,
    )
    sealed = forensic.emit(node, store=store)
    return sealed.forensic_uuid if sealed.falsifier_survived else None


def filter_output(text: str, identity: dict | None = None, store=None) -> dict:
    """Scan ``text``; on DENY prepend the evidence block + emit forensics.

    Returns ``{"text", "verdicts": [PreassertResult dicts], "forensic_ids"}``.
    Non-negative text passes through byte-identical.
    """
    verdicts = [preassert(s, store=store) for s in negative_sentences(text or "")]
    denials = [v for v in verdicts if v.verdict == VERDICT_DENY]
    forensic_ids = [fid for fid in (_seal(v, identity, store) for v in denials) if fid]
    out_text = text
    if denials:
        out_text = _annotation(denials) + "\n\n" + (text or "")
    return {
        "text": out_text,
        "verdicts": [v.to_dict() for v in verdicts],
        "forensic_ids": forensic_ids,
    }
