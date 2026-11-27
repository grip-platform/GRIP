"""Watch for V>>'s replies to pending questions, on whichever shim he used.

THE GAP THIS CLOSES. Everything else was built and none of it delivered:
questions were recorded, a board rendered, a reply router existed — and nothing
ever sent the board or listened for an answer. V>>, 2026-09-20: *"but where are
they? I don't see them on my Phone, on Slack, or Discord or WhatsApp. So how
must I answer?"* He is right, and Astra had named it in advance: **a cross-shim
reply ref is an address, not delivery.**

PRIVACY SCOPE, deliberately narrow. Standing instruction is never to read
`state/whatsapp-history/*.jsonl` — it is his private correspondence. This reads
ONE file, his own direct thread, and only to find HIS reply to a question GRIP
itself asked. It matches on the FIRST TOKEN only, against refs already open in
the pending store, and ignores everything else it passes. A message that is not
an answer to an outstanding GRIP question is not parsed, not stored and not
acted on.

WHY A POLLER AND NOT A SOCKET. The grip-channel daemon already owns the socket
and writes every inbound message to the per-JID JSONL. A second socket would be
a second consumer of the same Baileys session, which is the shape that breaks
WhatsApp connections. Tailing the file the daemon already writes is strictly
safer and needs nothing from the daemon.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from lib.pending_question import Pending, PendingStore
from lib.reply_router import deliver_answer
from lib.wa_identities import operator_jid

__all__ = [
    "OPERATOR_JID", "thread_path", "scan_for_answers", "sweep_overdue", "renotify",
    "operator_is_afk", "notices_held",
]

# GRIP_OPERATOR_JID, else the private file (lib/wa_identities.py). None means no
# operator is configured on this machine: nothing is watched and nothing is sent.
OPERATOR_JID = operator_jid("GRIP_OPERATOR_JID")


def thread_path(jid: str) -> Path:
    """The per-JID mirror the grip-channel daemon writes inbound messages to."""
    # NB: check the STRING, not Path(str). Path("") is PosixPath(".") and
    # therefore truthy, so an unset override silently resolved to the current
    # directory — a file that never exists, and a watcher that reads nothing
    # while looking like it works.
    override = os.environ.get("GRIP_WA_HISTORY_DIR", "").strip()
    base = Path(override) if override else (
        Path.home() / ".claude" / "state" / "whatsapp-history"
    )
    return base / f"{jid.replace('@', '_')}.jsonl"


def _inbound_texts(path: Path, limit: int = 40) -> list[str]:
    """The last `limit` INBOUND message bodies, newest last.

    Outbound rows are skipped: GRIP must never read its own notice as an answer
    to itself.
    """
    try:
        lines = path.read_text(errors="replace").splitlines()[-limit:]
    except (FileNotFoundError, OSError):
        return []
    out: list[str] = []
    for line in lines:
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        # ONLY "out-bot" is ours. Measured on the live store: the operator's
        # own thread contains 2,811 out-bot and 397 out-phone rows and ZERO
        # "in" rows, because the bot runs on HIS account — so a message he
        # types from his phone is recorded as OUT, not in.
        #
        # Two ways to get this wrong, and I made both in five minutes. Matching
        # direction == "out" exactly let GRIP read its own notice back as an
        # answer to itself. Then skipping everything starting with "out"
        # filtered out the only rows that carry his replies, which would have
        # shipped a watcher that silently read nothing.
        #
        # The honest rule is about AUTHORSHIP, not direction: skip what GRIP
        # sent, keep what a human typed. "in" is kept too, for threads where
        # the bot is not the operator's own account.
        direction = str(rec.get("direction") or "").lower()
        if direction == "out-bot" or rec.get("fromMe") or rec.get("from_me"):
            continue
        text = rec.get("text") or rec.get("body") or ""
        if isinstance(text, str) and text.strip():
            out.append(text.strip())
    return out


def scan_for_answers(
    store: PendingStore, *, jid: str | None = OPERATOR_JID, limit: int = 40
) -> list[tuple[str, bool, str]]:
    """Deliver any reply whose FIRST TOKEN is an open question's ref.

    Returns one (ref, delivered, reason) per message acted on. A message whose
    first token matches nothing open is ignored entirely — it is his ordinary
    correspondence and none of GRIP's business. With no operator configured
    there is no thread to read, so nothing is acted on.
    """
    open_refs = {p.ref for p in store.open_questions()}
    if not open_refs or not jid:
        return []

    results: list[tuple[str, bool, str]] = []
    for text in _inbound_texts(thread_path(jid), limit):
        head = text.split()
        if not head:
            continue
        token = head[0].strip("#:,.").lower()
        if token not in open_refs:
            continue  # not an answer to anything GRIP asked
        r = deliver_answer(store, text, via="whatsapp")
        results.append((token, r.delivered, r.reason))
        open_refs.discard(token)
    return results


# ---------------------------------------------------------------- the sweep

def _marker_seconds_left(path: Path, now) -> float | None:
    """Seconds left on one /afk marker, or None when it is unreadable, not a
    JSON object, not active, or carries no valid deadline.

    The deadline is an ISO-8601 string, and checking it is not optional: this
    directory holds markers from many past sessions, all still carrying
    active=true. Without the deadline, one stale marker from weeks ago
    silences every notice for ever. A deadline with no timezone is not valid:
    comparing it with the clock would mean guessing which zone it meant, and
    subtracting it from an aware time raises.
    """
    from datetime import datetime

    try:
        rec = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(rec, dict):
        return None
    raw = str(rec.get("deadline") or "") if rec.get("active") else ""
    if not raw:
        return None
    try:
        deadline = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if deadline.tzinfo is None:
        return None
    return (deadline - now).total_seconds()


def afk_remaining_seconds() -> float | None:
    """Seconds left on the longest LIVE /afk declaration, or None if none.

    The operator's own declaration of how long he is away. Two consumers want
    different halves of it: the answer-watcher wants the boolean (hold the
    notice), and the continuation brake wants the NUMBER (that is the window
    he sanctioned). Computing it once keeps them from disagreeing about which
    markers count as live.

    The directory comes from get_claude_dir(), as the writer's does
    (lib/session_mode.py). It used to be a $HOME literal, so with CLAUDE_DIR
    set the writer and this reader looked in different places (GRIP#6708).
    """
    from datetime import datetime, timezone

    from lib.claude_dir import get_claude_dir

    d = get_claude_dir() / "state" / "session"
    try:
        markers = list(d.glob("afk-*-mode.json")) + list(d.glob("afk-mode.json"))
    except OSError:
        return None
    now = datetime.now(timezone.utc)
    lefts = [_marker_seconds_left(m, now) for m in markers]
    live = [s for s in lefts if s is not None and s > 0]
    return max(live) if live else None


def operator_is_afk() -> bool:
    """True while ANY session holds a live /afk marker.

    A notice sent to someone who has explicitly said he is away is the
    definition of the noise this design exists to avoid — and unlike a guess
    about his availability from a clock, which
    `rules/never-close-on-the-operators-behalf.md` forbids, an /afk marker is
    HIS OWN DECLARATION. Parking still happens; only the ping waits.
    """
    return afk_remaining_seconds() is not None


def notices_held() -> bool:
    """True while the away marker or the outbound brake holds every notice.

    Neither spends a try: a held notice is not a failed one, so notices resume
    when the hold lifts (GRIP#10186 item 5).
    """
    if operator_is_afk():
        return True
    try:
        from lib.whatsapp_transport import STOP_MARKER
    except ImportError:
        return False
    return STOP_MARKER.exists()


def sweep_overdue(store: PendingStore, *, now: float | None = None) -> list[tuple[str, bool]]:
    """Park every question past the ceiling, whoever asked it.

    THE HOLE THIS CLOSES, found live: parking happened only inside the turn-end
    hook, so it required the ASKING session to take another turn. A session that
    asked a question and then went idle never parked it — the ceiling never
    fired, no notice was ever sent, and the question sat on a board nobody was
    looking at. Measured 2026-09-20: four questions open, all past the
    20-minute ceiling, two of them over TWO HOURS old, none parked, none
    alerted. That is precisely the "costly inaction" Astra predicted, happening
    in the system that predicted it.

    A timer, not a turn, is the right trigger: the question is overdue whether
    or not anybody takes a turn, which is exactly what made it invisible.

    Returns (ref, notified) per question parked.
    """
    import time as _t

    from lib.next_step_router import RELEASE_SECONDS

    now = _t.time() if now is None else now
    out: list[tuple[str, bool]] = []
    for q in store.open_questions():
        # An expired question is not current work: parking it now would page
        # the operator about something over a week old (GRIP#10186).
        if q.parked or q.is_expired(now) or (now - q.asked_at) < RELEASE_SECONDS:
            continue
        if not store.park(q.question_id):
            continue
        fresh = next((x for x in store.open_questions()
                      if x.question_id == q.question_id), None)
        notified = fresh is not None and not notices_held() and _send_notice(store, fresh, now)
        out.append((q.ref, notified))
    return out


def _send_notice(store: PendingStore, question: Pending, now: float) -> bool:
    """Send one question's parking notice, if it earns one, and record the attempt.

    Returns True only when the transport reports delivery. A delivered notice makes
    the question the holder of the one notice thread (GRIP#10186 item 5).
    """
    from lib.pending_question import parking_notice

    text = parking_notice(store, question, now)
    if not text or not OPERATOR_JID:
        return False
    try:
        from lib.whatsapp_transport import post
        # post() reports a refused or failed send as ok=False and raises only on
        # a malformed recipient, so "did not raise" is not "delivered". Record
        # what the transport said.
        sent = post(OPERATOR_JID, text + "\n\nReply here with the ref and your answer.")
        delivered = bool(sent.get("ok"))
    except Exception:
        delivered = False
    store.mark_noticed(question.question_id, now, delivered)
    return delivered


def renotify(store: PendingStore, *, now: float | None = None) -> tuple[str, bool] | None:
    """Send the parking notice that is due, if any: a retry, or the newest question's first.

    Codex on GRIP#10183: once the question holding the notice closed (answered,
    withdrawn or expired), the questions parked after it were never notified,
    because the sweep skips rows already parked. Now, with no holder, the newest
    live parked question gets its first notice. With a holder whose notice
    failed, that holder is tried again after NOTICE_RETRY_S, at most
    NOTICE_ATTEMPTS times in all. Nothing is sent while notices are held.
    Returns (ref, delivered) when a notice was tried, else None.
    """
    import time as _t

    now = _t.time() if now is None else now
    if notices_held() or not OPERATOR_JID:
        return None
    holder = store.notice_holder(now)
    target = _newest_parked(store, now) if holder is None else _due_retry(holder, now)
    return None if target is None else (target.ref, _send_notice(store, target, now))


def _newest_parked(store: PendingStore, now: float) -> Pending | None:
    """With no notice holder, the newest live parked question earns the next notice."""
    from lib.pending_question import live_questions

    parked = [p for p in live_questions(store, now) if p.parked]
    return max(parked, key=lambda p: p.asked_at, default=None)


def _due_retry(holder: Pending, now: float) -> Pending | None:
    """The holder again, when its notice failed, it has tries left and the pause has passed."""
    from lib.pending_question import NOTICE_ATTEMPTS, NOTICE_RETRY_S

    if holder.notice_sent_at is not None or holder.notice_attempts >= NOTICE_ATTEMPTS:
        return None
    return holder if now - (holder.notice_tried_at or 0.0) >= NOTICE_RETRY_S else None
