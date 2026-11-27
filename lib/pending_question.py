"""The cross-session store of questions GRIP is waiting on V>> to answer.

V>>'s core requisite, 2026-09-20: *"I need to be able to reply on any shim from
any session with the recommended next action if it's 'yours'."*

That one sentence decides the architecture. A question asked by session A and
answered on WhatsApp cannot live inside session A, because the WhatsApp bridge
has never heard of session A. The pending question must therefore live OUTSIDE
every session, in one place all of them and all the shims can read.

FOUR PROPERTIES, each load-bearing:

1. **One store, outside all sessions.** A reply arriving on any transport finds
   the question and the session that asked it.

2. **At most ONE question may hold an alert at a time, globally.** This is the
   notification budget and the addressing scheme in a single move. Because only
   one question has been alerted, a bare "yes" from a phone is never ambiguous —
   there is exactly one thing it can mean. A second session reaching (yours)
   while one is live does not alert; it parks silently. Hours before this was
   written V>> killed a monitor that had sent him 1,120 content-free alerts; a
   per-question budget would have permitted the same shape again, one polite
   notification at a time.

3. **The alert is plain language, because a person reads it on a phone.**
   Rule 17 applies in full and compression is forbidden: no paths, no session
   ids, no `(yours)`, no jargon. A sentence he understands in a supermarket
   queue, and one line telling him how to answer.

4. **Nothing executes on silence.** A question that times out is PARKED, not
   answered. That is what lets the alert honestly promise "nothing happens until
   you reply" — a sentence that would be a lie under any design that proceeds on
   a timer.
"""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import time
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

# The thresholds live with the router that reads the token — one definition,
# imported, never a second copy that can drift from it.
try:  # package import (PYTHONPATH=~/.grip)
    from lib.next_step_router import RELEASE_SECONDS, SILENT_WAIT_SECONDS
except ImportError:  # direct import with lib/ on the path
    from next_step_router import RELEASE_SECONDS, SILENT_WAIT_SECONDS

# Every read-modify-write of the store holds one lock across processes
# (GRIP#10171, a Codex finding): a whole-file write could otherwise erase what
# another writer recorded in between, such as an answer arriving while a
# withdrawal ran. The canonical lock is lib/file_lock.py, whose bare `_compat`
# import needs lib/ itself on sys.path, as lib/audit_chain.py notes. lib/ is
# appended, not inserted, so nothing in it can shadow another module.
_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.append(str(_LIB))
from file_lock import FileLock  # noqa: E402

# Well inside the recorder hook's 10-second deadline (config/hooks.registry.json),
# so a lock wait ends before the harness kills the hook (a Codex finding); a write
# takes milliseconds.
_LOCK_TIMEOUT_S = 3.0

# A question still open this long after it was asked has EXPIRED (GRIP#10186,
# rules/lifecycle-closed-or-expired.md). It stays open and unanswered: a reply
# that names it by its ref still reaches it, and its session may still withdraw
# it. But no board, alert or notice counts it as current work. By 2026-11-27,
# 737 parked questions had built up, and while any one of them stayed open no
# new parking notice was sent. Expiry is worked out at every read, so nothing
# is rewritten and no reaper is needed.
EXPIRY_S = 7 * 24 * 3600

# A parking notice that failed to send is tried again after this long, never on
# every 20-second sweep (GRIP#10186 item 5).
NOTICE_RETRY_S = 15 * 60

# ... and at most this many times in all. A failed try still holds the notice
# thread, so later questions stay silent, and a transport that reports failure
# for a message that did arrive can repeat it at most twice. The bound on the
# phone stays structural, as the anti-1,120 design requires.
NOTICE_ATTEMPTS = 3

__all__ = [
    "Pending",
    "PendingStore",
    "PendingReader",
    "PendingWriter",
    "plain_alert",
    "should_alert",
    "board",
    "UNDECLARED",
    "NOT_NEEDED",
    "parking_notice",
    "parked_board",
    "withdraw",
    "withdraw_refusal",
    "bare_session",
    "live_questions",
    "EXPIRY_S",
    "NOTICE_RETRY_S",
    "NOTICE_ATTEMPTS",
    "ALERT_LOCK",
    "SILENT_WAIT_SECONDS",
    "RELEASE_SECONDS",
]

# One well-known resource name, so the global alert budget is enforced through
# the advisory lock the presence substrate already provides.
ALERT_LOCK = "grip:next-step-alert"

# A harness session id. Anything else cannot be an inbox recipient.
_SESSION_ID = re.compile(
    r"^(cc-)?[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)

# Astra's sentinel (gpt-6-astra, 2026-09-20), answering a gap Fable opened.
#
# Fable: parking launders the agent's preference as the operator's, because an
# agent that parks and carries on must act on SOME answer and that answer is its
# own, unlabelled. Astra's refinement: the turn-end hook CANNOT know what a
# later turn will assume, so it can only honestly write "nothing declared" — and
# leaving that as None makes it indistinguishable from "nothing depended on it".
# Two very different states rendering identically is how the gap stays invisible.
#
# So parking writes UNDECLARED explicitly. Three states, all distinguishable:
#
#   UNDECLARED   parked, and whether anything downstream depends on it is
#                UNKNOWN. If work continues here, the assumption is unstated.
#   NOT_NEEDED   nothing downstream depended on the answer. Declared, not assumed.
#   <free text>  the agent proceeded as if this, and `default_source` says so.
#
# `default_source` is never "operator". Only an ANSWER is the operator's, and an
# answer populates `answer`/`answered_at` instead.
UNDECLARED = "UNDECLARED"
NOT_NEEDED = "NOT-NEEDED-nothing-downstream-depended-on-it"



def _from_row(row: dict) -> "Pending":
    """Build a Pending from a stored row, TOLERATING unknown fields.

    A bare ``Pending(**row)`` raises TypeError the moment any writer adds a
    field the reader does not know — and with many sessions running different
    checkouts, "the reader" is routinely older code. Observed live 2026-09-20:
    adding `default_source` made every running consumer throw
    "unexpected keyword argument 'default_source'" on every poll, while the
    store itself was perfectly valid.

    A shared, append-only store crossing process boundaries needs readers that
    ignore what they do not recognise. Dropping an unknown field loses
    information for THAT reader; raising loses the record for everyone.
    """
    known = {f.name for f in fields(Pending)}
    return Pending(**{k: v for k, v in row.items() if k in known})


@dataclass
class Pending:
    """One open question, owned by the session that asked it."""

    question_id: str
    session_id: str
    session_name: str
    asked_at: float
    summary: str                 # plain language, one sentence, for the phone
    step_text: str               # the verbatim RECOMMENDED NEXT STEP text
    alerted_at: float | None = None
    answered_at: float | None = None
    answer: str | None = None
    answered_via: str | None = None
    parked: bool = False
    # What the agent proceeded AS IF, when it parked this and carried on.
    # Fable's finding (claude-fable-5-1 via CCH, 2026-09-20), and the deepest
    # criticism this design received: "an agent that parks it and carries on
    # must act on SOME answer anyway — and that answer is its own default,
    # unlabelled... The ledger shows deference ('I asked'), the work shows a
    # decision ('I chose'). Parking has become a way to launder the agent's
    # preference as the operator's."
    #
    # So a parked question is not merely unanswered. If work continued, the
    # premise it continued on is the agent's own and is recorded AS the agent's,
    # where the operator can see it and overturn it. Left None when nothing
    # downstream depended on the answer.
    assumed_default: str | None = None
    # Who declared it. "agent" when the agent chose and said so; None when
    # nothing has been declared. NEVER "operator" — see UNDECLARED above.
    default_source: str | None = None
    # Astra's refinement (gpt-6-astra, in a genuinely multi-provider council,
    # 2026-09-20), naming the failure its own remedy creates: "downstream agents
    # treat a logged provisional default as operator authorization... require
    # explicit assumption records ONLY when subsequent actions actually depend
    # on them, with unresolved and unauthorized status preserved."
    #
    # So the record carries its own status in the DATA, not only in the prose a
    # human reads. A reader of this JSON cannot mistake a default for a
    # decision, because the field says what it is. It is deliberately a constant
    # rather than a flag: there is no value of this field that means authorised,
    # and an assumed default can never become one — only an ANSWER can, and an
    # answer populates `answer`/`answered_at` instead.
    assumed_default_status: str = "UNAUTHORIZED-agent-default-not-operator-decision"
    tags: list[str] = field(default_factory=list)
    # Presence Wave 3 (`lib/pending_materiality.py`), recorded in SHADOW mode: the
    # verdict rides on every question so it can be measured against real use
    # before anything is dropped because of it. "decision", "chore" or "unknown";
    # None on rows written before the field existed. Only "chore" means no
    # decision is needed, so None and "unknown" both render as decisions: the
    # expensive error is a real decision filed away as a chore, never a chore left
    # in view.
    materiality: str | None = None
    # The HALT class that makes it his, in plain words, or None when there is none.
    why_yours: str | None = None
    # A withdrawal (GRIP#10171): the session that asked closes a question whose
    # work was settled another way, such as an answer given inside the session or
    # a step done once he allowed it. It is never an answer: `answer` and
    # `answered_at` stay empty, so it authorises nothing.
    withdrawn_at: float | None = None
    withdrawn_reason: str | None = None
    withdrawn_by: str | None = None
    # The parking notice (GRIP#10186 item 5). The question whose notice was
    # tried, delivered or not, holds the one notice thread until it is answered,
    # withdrawn or expires. A failed notice is tried again, NOTICE_RETRY_S
    # apart and at most NOTICE_ATTEMPTS times in all.
    notice_sent_at: float | None = None
    notice_tried_at: float | None = None
    notice_attempts: int = 0

    @property
    def needs_decision(self) -> bool:
        """Everything except a named chore. Unknown and unclassified are decisions."""
        return self.materiality != "chore"

    @property
    def is_routable(self) -> bool:
        """Can an answer to this actually reach the session that asked?

        A record whose `session_id` is not a real session id can never be
        delivered: `deliver_answer` writes to the mesh inbox keyed on it, and a
        fabricated key routes nowhere. Showing such a question on the board
        presents something answerable that is not.

        This is not hypothetical. Test runs wrote entries into the LIVE store
        before the state-dir override existed, and one of them — session_id
        "s2" — reached a board sent to the operator's phone. He was shown a
        question no session had asked and no reply could have answered.
        """
        return bool(_SESSION_ID.match(self.session_id or ""))

    @property
    def ref(self) -> str:
        """Four characters a human can retype. Shown in the alert."""
        return self.question_id[:4]

    @property
    def is_open(self) -> bool:
        return self.answered_at is None and self.withdrawn_at is None

    def is_expired(self, now: float | None = None) -> bool:
        """Open, and asked more than EXPIRY_S ago. Expiry never answers anything."""
        now = time.time() if now is None else now
        return self.is_open and now - self.asked_at > EXPIRY_S


def _closed(row: dict) -> bool:
    """Answered or withdrawn: either way, nobody is waiting on it any more."""
    return row.get("answered_at") is not None or row.get("withdrawn_at") is not None


def bare_session(session_id: str | None) -> str:
    """A session id without the mesh's `cc-` prefix, so both spellings match."""
    sid = (session_id or "").strip()
    return sid[3:] if sid.startswith("cc-") else sid


def withdraw_refusal(question: Pending | None, reason: str, by_session: str) -> str | None:
    """Why withdrawing this question would be refused, in plain words, or None when allowed.

    One rule for PendingWriter.withdraw, which applies it under the lock, and for a
    command that explains a refusal afterwards (GRIP#10186 item 1b).
    """
    if question is None:
        return "there is no such question"
    if not question.is_open:
        return "the question is already closed"
    if not (reason or "").strip():
        return "no reason was given"
    if not _SESSION_ID.match(by_session or ""):
        return f"{by_session!r} is not a session id"
    if bare_session(question.session_id) != bare_session(by_session):
        return "only the session that asked may withdraw it"
    return None


class _PendingFile:
    """The store's one JSON file: read whole, and written atomically under one lock."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _read(self) -> dict[str, dict]:
        try:
            return json.loads(self.path.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            # A truncated or absent store means no pending questions, never a
            # crash — this sits on the turn-end path of every session.
            return {}

    def _write(self, data: dict[str, dict]) -> None:
        # Write-then-rename, so a reader on another session or shim never
        # observes a half-written store.
        fd, tmp = tempfile.mkstemp(dir=str(self.path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as fh:
                json.dump(data, fh, indent=2, sort_keys=True)
            os.replace(tmp, self.path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    def _locked(self) -> FileLock:
        """One writer at a time, across every session and shim, through a sidecar lock file."""
        return FileLock(self.path, sidecar=True, timeout=_LOCK_TIMEOUT_S)


class PendingReader(_PendingFile):
    """What a reader may ask of the store, and nothing that changes it (GRIP#10186).

    The boards, the notices and the reply count read through this role, so they
    cannot change the store by mistake. A read takes no lock: every write
    replaces the file whole, so a reader sees the old store or the new one.
    """

    def open_questions(self) -> list[Pending]:
        return [_from_row(v) for v in self._read().values() if not _closed(v)]

    def question(self, question_id: str) -> Pending | None:
        """The stored question with this id, open or closed, or None."""
        row = self._read().get(question_id)
        return None if row is None else _from_row(row)

    def alerted(self) -> Pending | None:
        """The one question currently holding the global alert, if any.

        An expired question never holds it. The sweep never parks an expired
        question, so without this check one could hold the budget for ever.
        """
        live = [p for p in live_questions(self) if p.alerted_at is not None and not p.parked]
        live.sort(key=lambda p: p.alerted_at or 0, reverse=True)
        return live[0] if live else None

    def may_alert(self) -> bool:
        """False when another question already holds the notification budget."""
        return self.alerted() is None

    def notice_holder(self, now: float | None = None) -> Pending | None:
        """The live parked question that holds the one parking-notice thread, if any.

        It is the question whose notice was tried, delivered or not. While it is
        open and not expired, a newly parked question joins the board without a
        notice, so a failing transport is never tried once per new question.
        """
        held = [p for p in live_questions(self, now) if p.parked and p.notice_tried_at is not None]
        return max(held, key=lambda p: p.notice_tried_at or 0.0, default=None)

    def resolve_reply(self, text: str) -> Pending | None:
        """Work out which question a reply from a phone is answering.

        Explicit ref wins. Otherwise, because only one question may hold an
        alert at a time, an un-addressed reply has exactly one possible target —
        which is the whole reason for the global budget. If nothing is alerted
        and exactly one live question is open, that one takes it; if several are
        open and none alerted, the reply is genuinely ambiguous and NOTHING is
        guessed, because guessing here answers the wrong question on his behalf.
        An expired question is reached only by its ref (GRIP#10186).
        """
        head = text.strip().split()
        if head:
            token = head[0].strip("#:,.").lower()
            for p in self.open_questions():
                if p.ref == token:
                    return p
            # A ref that names a question already answered or withdrawn addresses
            # THAT question, which is closed: the reply must never fall through to
            # another one (GRIP#10171, a Codex finding). The watcher picks a reply
            # by an open ref, and the question can close before it is delivered.
            if any(qid[:4] == token for qid in self._read()):
                return None
        live = self.alerted()
        if live is not None:
            return live
        openq = live_questions(self)
        return openq[0] if len(openq) == 1 else None


class PendingWriter(_PendingFile):
    """Every change to the store, each a read-modify-write under the one lock."""

    def add(self, pending: Pending) -> Pending:
        with self._locked():
            data = self._read()
            # Re-asking the same question does not create a second entry, which is
            # what stops a restarted or looping session earning a fresh alert.
            if pending.question_id in data:
                return _from_row(data[pending.question_id])
            data[pending.question_id] = asdict(pending)
            self._write(data)
        return pending

    def mark_alerted(self, question_id: str, when: float | None = None) -> bool:
        """Latch the alert. Returns False if it had already fired.

        The latch is the single property standing between this feature and the
        advisor V>> killed: `alert_once` has to BE once, not be named once.
        """
        with self._locked():
            data = self._read()
            row = data.get(question_id)
            if row is None or row.get("alerted_at") is not None:
                return False
            row["alerted_at"] = when if when is not None else time.time()
            self._write(data)
        return True

    def park(self, question_id: str, assumed_default: str | None = None) -> bool:
        """Park the question. It stays OPEN and UNANSWERED.

        `assumed_default` is what the agent is proceeding AS IF, in its own
        words, when downstream work cannot wait. Recording it is what stops
        parking from laundering the agent's preference as the operator's: the
        ledger then shows both halves — that he was asked, and what was chosen
        in his absence — instead of only the flattering one.
        """
        with self._locked():
            data = self._read()
            row = data.get(question_id)
            if row is None or _closed(row):
                return False
            row["parked"] = True
            # Absence of a declared default is itself a state worth recording, and
            # recording it is the whole point: None renders identically to "nothing
            # depended on this", which is how the gap hides.
            row["assumed_default"] = (
                assumed_default if assumed_default is not None else UNDECLARED
            )
            row["default_source"] = "agent" if assumed_default is not None else None
            self._write(data)
        return True

    def answer(self, question_id: str, text: str, via: str) -> Pending | None:
        """Record V>>'s reply, from whichever shim it arrived on."""
        with self._locked():
            data = self._read()
            row = data.get(question_id)
            if row is None or _closed(row):
                return None
            row["answered_at"] = time.time()
            row["answer"] = text
            row["answered_via"] = via
            self._write(data)
        return _from_row(row)

    def mark_noticed(self, question_id: str, when: float, delivered: bool) -> bool:
        """Record a parking-notice try, which makes this question the notice holder.

        Returns False when the question is missing or closed. It never answers anything.
        """
        with self._locked():
            data = self._read()
            row = data.get(question_id)
            if row is None or _closed(row):
                return False
            row["notice_tried_at"] = when
            row["notice_attempts"] = int(row.get("notice_attempts") or 0) + 1
            if delivered:
                row["notice_sent_at"] = when
            self._write(data)
        return True

    def withdraw(self, question_id: str, reason: str, by_session: str) -> Pending | None:
        """Close the asking session's own question when its work was settled another way.

        GRIP#10171: an answer given inside the session, or a step done once he allowed
        it, never reached this store, so such a question stayed open forever. Only the
        session that asked may withdraw it, and only with a reason. A withdrawal is
        never an answer: `answer` and `answered_at` stay empty, so nothing can read it
        as his decision, and a later reply finds the question closed. Returns None when
        `withdraw_refusal` names a refusal: the question is missing or already closed,
        the reason is blank, or `by_session` is not the asking session.

        Two sessions that end on the same step text share one row, because the
        question id comes from that text (the de-duplication that stops a restarted
        session earning a fresh alert). The row belongs to its first asker, and its
        withdrawal closes it for both, because the same text names the same step.
        `bin/grip-pending-withdraw` is the command a session runs.

        `by_session` is stored verbatim as `withdrawn_by`; `bare_session` drops the
        mesh's `cc-` prefix only to compare it with the asker. The ownership check
        stops one session closing another's question by mistake. It is not a
        security boundary: the store is a plain local file that any local process
        can write (GRIP#10186 item 3).
        """
        reason = (reason or "").strip()
        with self._locked():
            data = self._read()
            row = data.get(question_id)
            if withdraw_refusal(None if row is None else _from_row(row), reason, by_session):
                return None
            row["withdrawn_at"] = time.time()
            row["withdrawn_reason"] = reason
            row["withdrawn_by"] = by_session
            self._write(data)
        return _from_row(row)


class PendingStore(PendingReader, PendingWriter):
    """Atomic, concurrency-safe JSON store shared by every session and shim.

    It holds both roles. A caller that only reads can take a PendingReader
    instead, and then it cannot change the store by mistake.
    """


def live_questions(store: PendingReader, now: float | None = None) -> list[Pending]:
    """The open questions that are still current work: every one not expired."""
    return _live_and_expired(store.open_questions(), now)[0]


def _live_and_expired(
    questions: list[Pending], now: float | None = None
) -> tuple[list[Pending], list[Pending]]:
    """Split open questions in two, so no report counts an expired one as current work."""
    now = time.time() if now is None else now
    live: list[Pending] = []
    expired: list[Pending] = []
    for p in questions:
        (expired if p.is_expired(now) else live).append(p)
    return live, expired


def withdraw(store: PendingWriter, question_id: str, reason: str, by_session: str) -> Pending | None:
    """The function form of PendingWriter.withdraw, for callers that import it by name."""
    return store.withdraw(question_id, reason, by_session)


def plain_alert(pending: Pending) -> str:
    """The message that lands on his phone. Rule 17 — full plain English.

    No paths, no session ids, no internal vocabulary, no markdown that a
    messaging app will render as literal asterisks. The question first, because
    a lock screen shows about two lines. Then how to answer. Then the promise
    that nothing moves until he does — which is only honest because a timed-out
    question parks instead of proceeding.
    """
    summary = " ".join(pending.summary.split()).rstrip(".")
    return (
        f"{summary}?\n"
        f"\n"
        f"Reply with yes, no, or just tell me what you would rather do.\n"
        f"Nothing happens until you answer. (ref {pending.ref})"
    )


# --------------------------------------------------------------- the trigger

def should_alert(
    *,
    seconds_waited: float,
    independent_work_remains: bool,
    budget_free: bool,
    already_alerted: bool,
) -> bool:
    """Decide whether this question has earned the one global alert.

    THE CLOCK IS NECESSARY BUT NEVER SUFFICIENT, and that is a correction to the
    original specification rather than an embellishment of it.

    V>>'s rule as written fires on elapsed time alone: five minutes of silence
    earns a notification. But GRIP holds a PARAMOUNT rule —
    `never-close-on-the-operators-behalf.md` — whose entire content is that a
    clock carries no information about this operator's availability. He works
    with North Americans; a quiet South African evening is the middle of
    somebody's working day. That rule was written after sixteen violations in
    one evening, and its test is: *am I making a claim about his availability?*
    A bare five-minute timer that concludes "he is not responding, buzz his
    phone" is precisely that inference, automated and given a speaker.

    So the clock is kept as a FLOOR — under five minutes he is probably still
    reading — and the decision is moved onto a fact about the WORK, which GRIP
    can actually observe:

        alert only when the session would otherwise go idle with a decision
        outstanding.

    While independent work remains, GRIP does it and stays silent. The alert
    then carries real information — *this thread has stopped and only you can
    restart it* — instead of restating something he already knows, which is the
    exact content class that made 1,120 advisor notifications worthless.

    `budget_free` is machine-global, not per session: with many sessions running,
    a per-session budget still multiplies into the same flood one polite
    notification at a time. Only a global bound bounds the total.
    """
    if already_alerted or not budget_free:
        return False
    if seconds_waited < SILENT_WAIT_SECONDS:
        return False
    return not independent_work_remains


def board(store: PendingReader) -> str:
    """The pull surface: everything waiting on him, across every session.

    This is the primary mechanism and the push is the option layered on top. A
    board is read when he chooses, is always current, costs nothing while he is
    away, and — the property that matters with many sessions running — scales
    linearly with them instead of multiplying notifications by them.

    Decisions come first. Chores follow in their own section, which says that no
    decision is needed: listed rather than hidden, so a wrong verdict stays in
    view and can be corrected. With no chore open, the render is exactly what it
    was before the materiality verdict existed.
    """
    openq, expired = _live_and_expired(store.open_questions())
    openq.sort(key=lambda p: p.asked_at)
    if not openq:
        return _join_sections(["Nothing is waiting on you."], _expired_section(expired))

    routable = [p for p in openq if p.is_routable]
    decisions = [p for p in routable if p.needs_decision]
    chores = [p for p in routable if not p.needs_decision]
    return _join_sections(
        _board_decisions(decisions, chores),
        _chore_section(chores, _board_item, "chore"),
        _unroutable_section([p for p in openq if not p.is_routable]),
        _expired_section(expired),
    )


def _one_line(text: str) -> str:
    return " ".join(text.split()).rstrip(".")


def _join_sections(*sections: list[str]) -> str:
    """Non-empty sections, one blank line between each."""
    lines: list[str] = []
    for section in sections:
        if section:
            lines += ([""] if lines else []) + section
    return "\n".join(lines)


def _items(questions: list[Pending], render, *, question: bool) -> list[str]:
    lines: list[str] = []
    for p in questions:
        lines += render(p, question=question)
    return lines


def _why_yours_line(p: Pending) -> list[str]:
    return [f"        why yours: {p.why_yours}"] if p.why_yours else []


def _board_item(p: Pending, *, question: bool) -> list[str]:
    """One board entry: the step, then when, where and in what state."""
    mins = max(0, int((time.time() - p.asked_at) // 60))
    age = "just now" if mins < 1 else f"{mins} min ago" if mins < 90 else f"{mins // 60}h ago"
    state = "alerted" if p.alerted_at and not p.parked else "parked" if p.parked else "quiet"
    mark = "?" if question else ""
    return [f"  [{p.ref}] {_one_line(p.summary)}{mark}",
            f"        {age} · {p.session_name} · {state}"] + _why_yours_line(p)


def _board_decisions(decisions: list[Pending], chores: list[Pending]) -> list[str]:
    """The decisions, worded exactly as before unless only chores are open."""
    if chores and not decisions:
        return ["Nothing needs a decision from you."]
    return ([f"{len(decisions)} waiting on you:", ""]
            + _items(decisions, _board_item, question=True)
            + ["", "Reply with a ref and your answer, e.g. \"be89 yes\"."])


def _chore_section(chores: list[Pending], render, label: str) -> list[str]:
    """Chores after the decisions: shown, never hidden, never phrased as questions."""
    if not chores:
        return []
    plural = "" if len(chores) == 1 else "s"
    return ([f"{len(chores)} {label}{plural} — no decision needed, "
             "listed so nothing is hidden:", ""]
            + _items(chores, render, question=False))


def _unroutable_section(unroutable: list[Pending]) -> list[str]:
    # Shown separately and never as answerable: a reply to one of these
    # cannot reach anybody.
    if not unroutable:
        return []
    return ([f"{len(unroutable)} cannot be routed (no live session) — not answerable:"]
            + [f"  [{p.ref}] {_one_line(p.summary)}" for p in unroutable])


def _expired_section(expired: list[Pending]) -> list[str]:
    """One line for the expired questions, never folded into the live count (GRIP#10186)."""
    if not expired:
        return []
    noun = "question" if len(expired) == 1 else "questions"
    return [f"{len(expired)} older {noun}, unanswered for over {EXPIRY_S // 86400} days, "
            "kept on record but not current work."]


# ------------------------------------------------- the parking notice

# Astra's specification (gpt-6-astra via the HAL gateway, 2026-09-20), which
# replaced the design this module shipped with. Its opening line is the whole
# argument: "'No alerts' and 'no silent abandonment' are incompatible unless the
# operator reliably checks a pending-work inbox. A cross-shim ref alone does not
# resolve that."
#
# THREE CHANGES FROM THE ORIGINAL DESIGN, each one a correction:
#
#   TRIGGER — fire on PARKING, not on five minutes of silence. Not when the
#   question is asked, and not on every continuation. A question answered inside
#   the ceiling never notifies at all, which is 75.8% of them by measurement.
#
#   CHANNEL — ONE thread, appended to. A second parked question while the thread
#   is still unacknowledged is APPENDED WITHOUT A NEW PING. This is the property
#   that makes 1,120 impossible by construction rather than by restraint: the
#   notification count is bounded by acknowledgements, not by question volume.
#
#   CONTENT — the decision itself, the reply ref, WHAT IS BLOCKED, the
#   consequence of continued inaction, and the explicit line "Parked; unanswered;
#   no decision inferred from timeout." Astra's closing caution is carried in the
#   text because it is the part a reader gets wrong: reading the notice is not
#   answering it.
#
# The honest limit, in Astra's words: "This prevents UNREPORTED abandonment.
# Guaranteed intervention requires acknowledgment/escalation machinery; one
# notification cannot guarantee it."


def parking_notice(store: PendingReader, newly_parked: Pending, now: float | None = None) -> str | None:
    """The text to send when a question parks, or None to stay silent.

    Returns None when a parking notice is already outstanding — the thread is
    unacknowledged, so this question is appended to the board and the operator
    is NOT pinged again. Silence here is the feature. An expired question never
    earns a notice, and never counts as one outstanding (GRIP#10186). `now` is
    the caller's clock, so the sweep that parked the question decides its expiry
    and this notice on one reading.

    Outstanding means another live question holds the notice thread, because its
    notice was tried (item 5). Before, any other parked question counted, so a
    parked question that never got a notice, because the operator was away or
    the send failed, silenced every later one.
    """
    now = time.time() if now is None else now
    holder = store.notice_holder(now)
    held_elsewhere = holder is not None and holder.question_id != newly_parked.question_id
    if newly_parked.is_expired(now) or held_elsewhere:
        return None  # expired, or another question holds the thread; append, do not ping

    summary = " ".join(newly_parked.summary.split()).rstrip(".")
    waited = max(1, int((now - newly_parked.asked_at) // 60))
    return (
        f"Decision needed — {newly_parked.ref}: {summary}?\n"
        f"\n"
        f"I held this for {waited} minutes and then carried on with other work. "
        f"It is parked, unanswered — no decision has been inferred from the "
        f"timeout, and nothing has been sent or published on your behalf.\n"
        f"\n"
        f"Reply \"{newly_parked.ref} <your answer>\" from any shim."
        + (
            f"\n\nWhile you were away I proceeded as if: {newly_parked.assumed_default}. "
            "That was my choice, not yours — say so and I will undo it."
            if newly_parked.assumed_default
            and newly_parked.assumed_default not in (UNDECLARED, NOT_NEEDED)
            else ""
        )
    )


def parked_board(store: PendingReader) -> str:
    """Everything parked and unanswered — the surface the notice points at.

    Decisions first, then chores in their own section, exactly as on `board`.
    Expired questions get one line of their own, as on `board`.
    """
    live, expired = _live_and_expired([p for p in store.open_questions() if p.parked])
    parked = sorted(live, key=lambda p: p.asked_at)
    if not parked:
        return _join_sections(["Nothing is parked."], _expired_section(expired))
    decisions = [p for p in parked if p.needs_decision]
    chores = [p for p in parked if not p.needs_decision]
    return _join_sections(
        _parked_decisions(decisions),
        _chore_section(chores, _parked_item, "parked chore"),
        _expired_section(expired),
        ["Reading this is not answering it — reply with a ref."],
    )


def _parked_decisions(decisions: list[Pending]) -> list[str]:
    if not decisions:
        return ["Nothing parked needs a decision from you."]
    return ([f"{len(decisions)} parked, unanswered:", ""]
            + _items(decisions, _parked_item, question=True))


def _parked_item(p: Pending, *, question: bool) -> list[str]:
    """One parked entry, then what the agent assumed while it waited."""
    mins = max(1, int((time.time() - p.asked_at) // 60))
    age = f"{mins} min ago" if mins < 90 else f"{mins // 60}h ago"
    mark = "?" if question else ""
    return ([f"  [{p.ref}] {_one_line(p.summary)}{mark}  ({age})"]
            + _why_yours_line(p) + _default_note(p.assumed_default))


def _default_note(assumed: str | None) -> list[str]:
    if assumed == UNDECLARED:
        return ["        no default declared — if work continued here, the assumption is UNSTATED"]
    if assumed == NOT_NEEDED:
        return ["        nothing downstream depended on this"]
    if assumed:
        return [f"        proceeded as if: {assumed}  (my choice, not yours)"]
    return []
