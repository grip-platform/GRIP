"""WhatsApp delivery verification — close the send loop from our own side.

Origin: V>> 2026-07-26 — "it's trivial to close the loop yourself, by reading
my own WhatsApp after sending." The transport's ok/message_id is send-side
acknowledgement only (a proven phantom); the local Baileys history log is the
self-service ground truth. This module makes send-then-verify ONE call so the
loop closes by construction instead of by discipline.

Public API::

    from lib.wa_delivery_verify import post_verified, send_file_verified

    r = post_verified("27000000000@s.whatsapp.net", "hello")
    # r: {ok, message_id, error, recipient, verified, delivery: {...}}

    r = send_file_verified("27000000000@s.whatsapp.net", "/path/doc.pdf",
                           caption="the doc")

Ground truth: ``state/whatsapp-history/<jid>.jsonl`` (``@`` -> ``_`` in the
filename), one JSON object per line with keys ``chat, direction, messageId,
sender, text, ts, type``. Outbound entries carry ``direction`` values like
``out-bot`` / ``out-phone``; documents log as ``type: "document"``.

Failure semantics mirror the transport shim: verification NEVER raises — it
returns ``verified: False`` with a reason. A missing/unreadable history file
is an honest ``verified: False`` (reason ``history-unavailable``), never a
crash and never a silent pass.

Falsifier: this module is wrong if the bridge stops appending outbound sends
to the history log (then verified=False on every genuinely delivered send —
detectable immediately) or renames the schema keys. Verifier:
``tests/test_wa_delivery_verify.py`` (hermetic — fake transport, tmp history).
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Optional

_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))
from claude_dir import get_claude_dir  # noqa: E402
try:  # package context (lib.wa_delivery_verify) binds lib.whatsapp_transport;
    from . import whatsapp_transport as _wt  # noqa: E402
except ImportError:  # flat context (scripts/relay with lib on sys.path) binds flat form
    import whatsapp_transport as _wt  # noqa: E402

# CALL-TIME binding (incident 2026-07-26): binding `post`/`call_tool` at import
# time froze references the existing test suites could not intercept — their
# monkeypatch of lib.whatsapp_transport.post no longer stubbed the send, and a
# test run leaked real WhatsApp messages to the operator. Resolving through the
# module attribute at every call restores that seam for every consumer.

__all__ = [
    "history_path",
    "history_baseline",
    "new_outbound_entries",
    "verify_delivery",
    "post_verified",
    "send_file_verified",
]

_POLL_S = 0.5


def _default_timeout() -> float:
    """Verify-poll budget; env-tunable so test suites can set it to 0."""
    try:
        return float(os.environ.get("GRIP_WA_VERIFY_TIMEOUT_S", "10"))
    except ValueError:
        return 10.0


def history_path(recipient: str, history_dir: Optional[Path] = None) -> Path:
    """The per-JID history log: ``@`` becomes ``_`` in the filename."""
    root = Path(history_dir) if history_dir else get_claude_dir() / "state" / "whatsapp-history"
    return root / f"{recipient.replace('@', '_')}.jsonl"


def history_baseline(recipient: str, history_dir: Optional[Path] = None) -> int:
    """Byte offset of the history log BEFORE a send (0 when absent)."""
    try:
        return history_path(recipient, history_dir).stat().st_size
    except OSError:
        return 0


def new_outbound_entries(
    recipient: str, offset: int, history_dir: Optional[Path] = None
) -> list[dict]:
    """Outbound entries appended after ``offset``. Unreadable file -> []."""
    path = history_path(recipient, history_dir)
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            fh.seek(offset)
            tail = fh.read()
    except OSError:
        return []
    entries: list[dict] = []
    for line in tail.splitlines():
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(rec, dict) and str(rec.get("direction", "")).startswith("out"):
            entries.append(rec)
    return entries


def verify_delivery(
    recipient: str,
    offset: int,
    *,
    expect_type: str = "text",
    expect_count: int = 1,
    message_id: Optional[str] = None,
    timeout_s: Optional[float] = None,
    history_dir: Optional[Path] = None,
) -> dict:
    """Poll the history log until the expected outbound entries appear.

    Matching: ``type == expect_type`` and outbound direction; when
    ``message_id`` is given AND present in the log's ID space, it must match.
    Returns ``{verified, matched, expected, entries, waited_s, history, reason}``.
    """
    if timeout_s is None:
        timeout_s = _default_timeout()
    path = history_path(recipient, history_dir)
    deadline = time.monotonic() + max(0.0, timeout_s)
    waited_start = time.monotonic()
    matched: list[dict] = []
    while True:
        candidates = [
            e for e in new_outbound_entries(recipient, offset, history_dir)
            if e.get("type") == expect_type
        ]
        if message_id:
            id_hits = [e for e in candidates if e.get("messageId") == message_id]
            matched = id_hits if id_hits else candidates
        else:
            matched = candidates
        if len(matched) >= expect_count:
            break
        if time.monotonic() >= deadline:
            break
        time.sleep(_POLL_S)
    verified = len(matched) >= expect_count
    reason = ""
    if not verified:
        reason = "history-unavailable" if not path.exists() else "no-matching-outbound-entry"
    return {
        "verified": verified,
        "matched": len(matched),
        "expected": expect_count,
        "entries": [
            {"messageId": e.get("messageId"), "type": e.get("type"), "ts": e.get("ts")}
            for e in matched
        ],
        "waited_s": round(time.monotonic() - waited_start, 2),
        "history": str(path),
        "reason": reason,
    }


def _local_bridge_absent() -> bool:
    """True when THIS host has no local WhatsApp bridge (SSH-egress host).

    On such a host (e.g. the VPS) the history log lives on the bridge-holder,
    so local polling is structurally futile — verification must come back
    embedded in the egress result instead (see ``post_verified`` passthrough).
    Lazy import keeps this module loadable without the transport's env state.
    """
    return bool(_wt._resolve_wa_ssh_host())


def post_verified(
    recipient: str,
    text: str,
    *,
    base_url: Optional[str] = None,
    timeout_s: Optional[float] = None,
    history_dir: Optional[Path] = None,
) -> dict:
    """``whatsapp_transport.post`` + history verification in one call.

    Verification happens exactly once, WHERE the history log lives: when the
    transport routed via SSH egress and the bridge host already ran
    ``post_verified`` (``lib/wa_egress_stdin.py``), the returned dict carries
    ``verified`` — that remote verdict is passed through untouched, never
    overwritten by a doomed local poll. A bridge-less host that receives no
    remote verdict reports ``verified: False`` immediately (no polling stall).
    """
    offset = history_baseline(recipient, history_dir)
    # base_url passed only when set — keeps legacy (recipient, text) fakes valid
    kw = {"base_url": base_url} if base_url is not None else {}
    result = _wt.post(recipient, text, **kw)
    if not result.get("ok"):
        return {**result, "verified": False,
                "delivery": {"reason": "send-failed", "matched": 0, "expected": 1}}
    if "verified" in result:  # bridge-side verdict rode back over the egress
        result.setdefault("delivery", {"reason": "remote-egress-verdict",
                                       "matched": int(bool(result["verified"])),
                                       "expected": 1})
        return result
    if _local_bridge_absent():
        return {**result, "verified": False,
                "delivery": {"reason": "remote-egress-unverified",
                             "matched": 0, "expected": 1}}
    delivery = verify_delivery(
        recipient, offset, expect_type="text",
        message_id=result.get("message_id"),
        timeout_s=timeout_s, history_dir=history_dir,
    )
    return {**result, "verified": delivery["verified"], "delivery": delivery}


def send_file_verified(
    recipient: str,
    file_path: str,
    caption: str = "",
    *,
    timeout_s: Optional[float] = None,
    history_dir: Optional[Path] = None,
) -> dict:
    """``channel_send_file`` + history verification in one call.

    Consults the STOP brake indirectly: the file send goes through the MCP
    tool, and the caller-visible contract mirrors ``post_verified``. On a
    bridge-less host the local MCP daemon has no WhatsApp leg at all, so the
    unverifiable case is reported immediately rather than polled for.
    """
    if _local_bridge_absent():
        offset = 0
    else:
        offset = history_baseline(recipient, history_dir)
    try:
        ack = _wt.call_tool(
            "channel_send_file",
            {"recipient": recipient, "file_path": file_path, "caption": caption},
        )
        result = {"ok": True, "ack": ack, "error": None, "recipient": recipient}
    except Exception as exc:  # transport raises on failure — fold to contract
        return {"ok": False, "ack": None, "error": str(exc), "recipient": recipient,
                "verified": False,
                "delivery": {"reason": "send-failed", "matched": 0, "expected": 1}}
    if _local_bridge_absent():
        return {**result, "verified": False,
                "delivery": {"reason": "remote-egress-unverified",
                             "matched": 0, "expected": 1}}
    delivery = verify_delivery(
        recipient, offset, expect_type="document",
        timeout_s=timeout_s, history_dir=history_dir,
    )
    return {**result, "verified": delivery["verified"], "delivery": delivery}


def main(argv: Optional[list] = None) -> int:
    """CLI: show the last outbound entries for a JID (ops spot-check)."""
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        print("usage: wa_delivery_verify.py <jid> [n]", file=sys.stderr)
        return 2
    jid, n = args[0], int(args[1]) if len(args) > 1 else 5
    entries = new_outbound_entries(jid, 0)
    for e in entries[-n:]:
        print(json.dumps({k: e.get(k) for k in ("ts", "type", "direction", "messageId")}))
    print(f"{len(entries)} outbound entries total in {history_path(jid)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
