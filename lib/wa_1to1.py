"""WhatsApp 1:1 gg++ shim — enhanced outbound send with retry + audit.

Public API::

    from lib.wa_1to1 import send, send_to_principals

    result = send(
        recipient="27000000000@s.whatsapp.net",
        text="Hello from GRIP",
    )
    # result: {ok: bool, message_id: str|None, error: str|None,
    #          recipient: str, attempts: int}

    results = send_to_principals(text="Update for all principals")
    # results: list[dict]  — one result dict per principal

Design
------
Wraps lib.whatsapp_transport.post with three enhancements over the base shim:

1. Retry with exponential backoff (up to _MAX_ATTEMPTS attempts).
2. Audit logging to data/wa-audit.jsonl — every attempt, success or fail.
3. Principal-broadcast helper: ``send_to_principals`` targets the configured
   principals (today V>> alone) via sequential sends (no partial-delivery
   without record).

Accepts both 1:1 (@s.whatsapp.net) and group (@g.us) JIDs — the underlying
``whatsapp_transport.post`` validates format; this shim passes validation
through transparently.

Syscall-doctrine compliant: stable provider-agnostic API, no SDK types
exposed, injectable ``_poster`` for tests (default = whatsapp_transport.post).

Falsifier
---------
This shim is wrong if whatsapp_transport.post changes its return contract
(then ``_is_ok`` must be updated) or if the audit log path moves
(then ``_AUDIT_LOG`` must be updated). Tests cover both the retry ladder
and the audit-write; a mutation that drops the audit write will fail
test_send_writes_audit_log.
"""

from __future__ import annotations

import datetime
import json
import re
import time
import uuid
from pathlib import Path
from typing import Callable, Optional

from lib.claude_dir import get_claude_dir
from lib.wa_identities import operator_jid

__all__ = ["send", "send_to_principals", "PRINCIPAL_V"]

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# From the private file (lib/wa_identities.py); None when not configured.
PRINCIPAL_V = operator_jid()

# Default broadcast targets: the operator alone. Adding a principal is a
# deliberate operator decision.
_PRINCIPALS = [p for p in (PRINCIPAL_V,) if p]  # empty: nobody to broadcast to
_MAX_ATTEMPTS = 3
_BACKOFF_BASE_S = 1.0          # doubles each retry: 1s, 2s, 4s

# WhatsApp JID pattern — non-empty identifier before ``@``, followed by exactly
# one of the two known WhatsApp suffixes. Tightens the prior ``"@" in recipient``
# substring check that admitted malformed JIDs like ``"@s.whatsapp.net"`` (empty
# identifier) and ``"abc@notwhatsapp.com"`` (unknown suffix). HAL Council
# concern, PR #3159 iteration 1, lib/wa_1to1.py JID validation.
_JID_RE = re.compile(r"^[\w.\-]+@(s\.whatsapp\.net|g\.us)$")

# Audit log path — resolved via the canonical CLAUDE_DIR portability shim per
# ``rules/path-portability.md`` (no inline ``Path.home()`` reimplementation).
# HAL Council concern, PR #3159 iteration 2, audit-log path resolution.
_CLAUDE_DIR = get_claude_dir()
_AUDIT_LOG = _CLAUDE_DIR / "data" / "wa-audit.jsonl"
try:
    _AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
except Exception:  # noqa: BLE001
    pass


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def send(
    recipient: str,
    text: str,
    *,
    _poster: Optional[Callable] = None,
    _sleep: Optional[Callable] = None,
) -> dict:
    """Send *text* to *recipient* with retry + audit.

    Args:
        recipient: WhatsApp JID (``<digits>@s.whatsapp.net`` or ``<id>@g.us``).
        text: Plain-text message body.
        _poster: Injectable send callable for tests
                 (default: ``whatsapp_transport.post``).
        _sleep:  Injectable sleep callable for tests (default: ``time.sleep``).

    Returns:
        dict with keys ``ok`` (bool), ``message_id`` (str|None),
        ``error`` (str|None), ``recipient`` (str), ``attempts`` (int).

    Raises:
        ValueError: if *recipient* is not a valid WhatsApp JID.
    """
    # Validate JID early — pattern-tight regex rejects empty identifiers and
    # unknown suffixes the prior substring check admitted.
    if not recipient or not _JID_RE.match(recipient):
        raise ValueError(
            f"Invalid WhatsApp JID {recipient!r}. "
            "Expected '<digits>@s.whatsapp.net' or '<id>@g.us'."
        )

    poster = _poster or _default_poster()
    sleeper = _sleep or time.sleep
    call_id = uuid.uuid4().hex[:12]
    last_result: dict = {"ok": False, "message_id": None, "error": None, "recipient": recipient}

    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            result = poster(recipient=recipient, text=text)
            if not isinstance(result, dict):
                result = {
                    "ok": False,
                    "message_id": None,
                    "error": f"transport_non_dict: {type(result).__name__}",
                    "recipient": recipient,
                }
        except Exception as exc:  # noqa: BLE001
            result = {
                "ok": False,
                "message_id": None,
                "error": f"transport_exception: {exc}",
                "recipient": recipient,
            }
        last_result = result
        _audit(call_id, attempt, recipient, result)

        if _is_ok(result):
            return {**result, "attempts": attempt}

        if attempt < _MAX_ATTEMPTS:
            sleeper(_BACKOFF_BASE_S * (2 ** (attempt - 1)))

    return {**last_result, "attempts": _MAX_ATTEMPTS}


def send_to_principals(
    text: str,
    *,
    principals: Optional[list[str]] = None,
    _poster: Optional[Callable] = None,
    _sleep: Optional[Callable] = None,
) -> list[dict]:
    """Broadcast *text* to all GRIP steering principals.

    Args:
        text: Plain-text message body.
        principals: Override recipient list (default: V>> alone).
        _poster: Injectable for tests.
        _sleep:  Injectable for tests.

    Returns:
        List of result dicts (one per principal, in order).
    """
    targets = principals if principals is not None else list(_PRINCIPALS)
    return [
        send(recipient=jid, text=text, _poster=_poster, _sleep=_sleep)
        for jid in targets
    ]


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _default_poster() -> Callable:
    """Late-import the delivery-verified sender so the module loads without the
    MCP daemon. ``post_verified`` returns a superset of the old ``post``
    contract (adds ``verified``/``delivery``); retry stays keyed on ``ok``
    (send acceptance), so a slow history append can never trigger a double
    send — ``verified`` flows through to callers and the audit log."""
    from lib.wa_delivery_verify import post_verified  # noqa: PLC0415
    return post_verified


def _is_ok(result: object) -> bool:
    return isinstance(result, dict) and bool(result.get("ok"))


def _audit(call_id: str, attempt: int, recipient: str, result: dict) -> None:
    """Append one line to the audit log. Never raises — audit failure is silent."""
    try:
        entry = {
            "ts": _utcnow(),
            "call_id": call_id,
            "attempt": attempt,
            "recipient": recipient,
            "ok": result.get("ok"),
            "message_id": result.get("message_id"),
            "error": result.get("error"),
        }
        with _AUDIT_LOG.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")
    except Exception:  # noqa: BLE001
        pass


def _utcnow() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()
