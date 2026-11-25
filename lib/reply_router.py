"""Deliver V>>'s answer from ANY shim to the session that asked the question.

This is `send_to_awaiting_session(V>>_response_received)` from the operator's
specification, and it is the half of the rule with no safety tension in it at
all: a reply he typed, delivered to the session waiting for it. Everything
dangerous lives on the timeout path; this path only moves his own words.

His requirement, 2026-09-20: *"I need to be able to reply on any shim from any
session with the recommended next action if it's 'yours'."*

THE THREE PIECES, and why each is where it is:

1. The QUESTION lives outside every session (lib/pending_question.py), because a
   question asked by session A and answered on WhatsApp cannot live in session
   A — the WhatsApp bridge has never heard of session A.

2. The ADDRESSING is free, because at most one question may hold the global
   alert at a time. A bare "yes" from a phone therefore has exactly one possible
   target. Where it genuinely does not, nothing is guessed: answering the wrong
   question on his behalf is worse than asking which he meant.

3. The DELIVERY is the mesh inbox — a real SQLite table
   (~/.grip-session-mesh/presence.db) already drained into live sessions by
   hooks/mesh-inbox-poll-hook.py. Nothing new is invented; the reply is written
   where cross-session messages already go.
"""

from __future__ import annotations

import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from lib.pending_question import Pending, PendingStore

__all__ = ["DeliveryResult", "mesh_db_path", "deliver_answer"]


def mesh_db_path() -> Path:
    """The live presence database, env-overridable exactly as bin/mesh does it."""
    return Path(
        os.environ.get(
            "CLAUDE_MESH_DB_PATH",
            os.path.expanduser("~/.grip-session-mesh/presence.db"),
        )
    )


class DeliveryResult:
    """What happened to one reply. Never raises at the caller."""

    def __init__(self, delivered: bool, reason: str, pending: Pending | None = None) -> None:
        self.delivered = delivered
        self.reason = reason
        self.pending = pending

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"DeliveryResult(delivered={self.delivered}, reason={self.reason!r})"


def deliver_answer(
    store: PendingStore,
    reply_text: str,
    *,
    via: str,
    db_path: Path | None = None,
) -> DeliveryResult:
    """Route one reply from a shim to the session that is waiting for it.

    `via` names the transport the answer arrived on (whatsapp, slack, ...) and is
    recorded with the answer, so the file shows where a decision came from.

    Ambiguity is REFUSED rather than resolved. With several questions open and
    none alerted, a bare reply has no honest target, and picking one would put
    words in his mouth against a question he was not looking at.
    """
    text = (reply_text or "").strip()
    if not text:
        return DeliveryResult(False, "empty reply")

    pending = store.resolve_reply(text)
    if pending is None:
        n = len(store.open_questions())
        return DeliveryResult(
            False,
            "nothing is waiting" if n == 0
            else f"ambiguous: {n} questions open and none alerted — needs a ref",
        )

    answered = store.answer(pending.question_id, text, via=via)
    if answered is None:
        return DeliveryResult(False, "already answered", pending)

    db = db_path or mesh_db_path()
    if not db.exists():
        # The answer is RECORDED even when it cannot be delivered. A reply that
        # is lost because a daemon is down is worse than one that waits in the
        # store for the session to read.
        return DeliveryResult(False, f"recorded, but mesh db absent at {db}", answered)

    try:
        conn = sqlite3.connect(str(db), timeout=5.0)
        try:
            conn.execute(
                "INSERT INTO inbox(id, recipient_id, from_id, from_name, kind, body,"
                " delivered, created_at) VALUES(?,?,?,?,?,?,0,?)",
                (
                    uuid.uuid4().hex,
                    pending.session_id,
                    "operator",
                    "V>>",
                    "next-step-answer",
                    f"[answer to {pending.ref}] {text}",
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            conn.commit()
        finally:
            conn.close()
    except sqlite3.Error as exc:
        return DeliveryResult(False, f"recorded, but delivery failed: {exc}", answered)

    return DeliveryResult(True, f"delivered to {pending.session_name} via {via}", answered)
