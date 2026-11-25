#!/usr/bin/env python3
"""Mac-side WhatsApp egress — forced-command target for the VPS relay.

B-done-safely (WA transport council wf_7ab9628f-aac): the VPS has no local
WhatsApp bridge (grip-channel there is Slack+Discord only; the Hetzner IP is
blocked at the Baileys handshake). To send a WhatsApp reply, the VPS relay
SSHes to the Mac — which holds the live Baileys bridge on 127.0.0.1:3101 — and
runs THIS fixed script under an ``authorized_keys`` ``command="..."``
forced-command, passing the payload over STDIN.

Why this shape is the *safe* form of SSH egress:
  * It takes NO argv — the recipient JID and the (LLM-generated, inbound-
    derived) reply text arrive ONLY as a JSON value on STDIN, so untrusted
    text never reaches a shell argv (no injection surface).
  * The Mac ``authorized_keys`` forced-command pins the relay key to THIS
    script alone — a compromised relay can send one WhatsApp message (bounded),
    never get a shell on the operator's personal machine (the council's
    blast-radius objection to raw SSH does not apply).
  * It routes through ``whatsapp_transport.post``, which is itself STOP-braked
    (state/session/outbound-stop.json) and JID-validated — so ``grip-stop on``
    ON THE MAC halts VPS-originated sends too (single authoritative brake axis,
    rules/full-power-stop-override.md).

Contract: read one JSON object ``{"recipient": "<jid>", "text": "<msg>"}`` from
STDIN; print the ``wa_delivery_verify.post_verified`` result dict as JSON to
STDOUT — a superset of the old ``whatsapp_transport.post`` contract that adds
``verified``/``delivery`` (bridge-local history verification, so the VPS gets
a real delivery verdict back over the SSH channel). Exit 0 = sent, 1 = send
failed (brake/transport; verification lag never fails the exit), 2 = bad payload.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

from wa_delivery_verify import post_verified as post  # noqa: E402 — braked + JID-validated + history-verified (verdict rides back over SSH)


def main() -> int:
    raw = sys.stdin.read()
    try:
        data = json.loads(raw)
        recipient = data["recipient"]
        text = data["text"]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        print(json.dumps({"ok": False, "error": f"bad payload: {exc}"}))
        return 2
    result = post(recipient=recipient, text=text)
    print(json.dumps(result))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
