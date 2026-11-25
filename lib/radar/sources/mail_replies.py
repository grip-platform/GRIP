"""The reply watch: did anyone answer the TMIF implementer-feedback mail?

Reads the Workspace mailbox through ``lib/grasp_google`` (the ``gog`` binary with
``--readonly --gmail-no-send --no-input`` pinned in its one argv builder), so this
source can never send, reply or open a link.

gog's search is THREAD-level: a reply does not add a row, it changes the existing
thread's date and message count (measured 2026-10-20 on the real thread: one row,
keys ``id, date, internalDateIso, from, subject, messageCount``). So each tick
fetches every matching thread and diffs message ids against ``seen_message_ids``.
Threads whose last activity is more than a day before the send are not fetched.

Modes. ``search`` runs the subject query and the bounce query (a mailer-daemon
report arrives as its own thread). The first reply in a thread the operator
wrote in switches the slice to ``thread_watch``, which fetches that thread and
the bounce query's threads (every 2 h, ``schedule.py``). Messages older than the
send are baseline even when their thread wakes up later.

Failure. When gog raises (token expired, binary missing) the service-account
route (``lib/meet_absorb_dwd.list_gmail_messages``) is tried only when its key
file exists; otherwise ``failing_since`` is set and ONE line goes out per SAST
day. ``skipped`` is set too, so ``last_run`` stays and the next tick retries.
"""
from __future__ import annotations

import email.utils
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from ..model import Result, change
from ..schedule import iso, parse_iso, sast_day
from ..state import cap

NAME = "mail_replies"
PREDATE_MARGIN = timedelta(days=1)   # gog prints thread dates to the minute, in local time
DEFAULT_BOUNCE_SENDERS = ("mailer-daemon", "postmaster")
BOUNCE_SUBJECTS = ("undeliverable", "delivery status notification", "mail delivery failed",
                   "returned mail", "delivery failure")
FIXTURE_SELF = "v@example.com"


@dataclass
class MailDeps:
    """The I/O one ``collect()`` needs, injected.

    ``search(account, query, max_results)`` and ``thread(account, thread_id)``
    return receipts shaped as ``lib/grasp_google.gmail_search`` and
    ``gmail_thread`` return them. ``self_addresses()`` gives the operator's own
    addresses. ``fallback(slice)`` returns message receipts from another route,
    or None when that route has no key. ``errors`` are the exception types that
    mean "the mail route failed" rather than "radar has a bug".
    """

    search: Callable[[str, str, int], list[dict]]
    thread: Callable[[str, str], dict]
    self_addresses: Callable[[], set[str]]
    fallback: Callable[[dict], list[dict] | None] | None = None
    errors: tuple[type[BaseException], ...] = (RuntimeError, OSError, ValueError)


def parse_mail_date(raw) -> datetime | None:
    """gog prints ``2026-10-20 17:32`` (the machine's local clock) at thread level
    and an RFC 2822 ``Date`` header per message. Either becomes aware UTC."""
    if not isinstance(raw, str) or not raw.strip():
        return None
    text = raw.strip()
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            dt = email.utils.parsedate_to_datetime(text)
        except (TypeError, ValueError):
            return None
    if dt.tzinfo is None:
        dt = dt.astimezone()  # naive means the machine's local clock, which is what gog prints
    return dt.astimezone(timezone.utc)


def sender_address(raw) -> str:
    text = str(raw or "")
    return (email.utils.parseaddr(text)[1] or text).strip().lower()


def sender_display(raw) -> str:
    name, addr = email.utils.parseaddr(str(raw or ""))
    return f"{name} <{addr}>" if name and addr else (addr or str(raw or "").strip())


def strip_re(subject) -> str:
    text = str(subject or "").strip()
    while text.lower().startswith(("re:", "fwd:", "fw:")):
        text = text.split(":", 1)[1].strip()
    return text


def classify(message: dict, selves: set[str], bounce_senders=DEFAULT_BOUNCE_SENDERS) -> str:
    """``self`` | ``bounce`` | ``reply``."""
    addr = sender_address(message.get("from"))
    if addr in selves:
        return "self"
    local_part = addr.split("@", 1)[0]
    if any(local_part.startswith(b) for b in bounce_senders):
        return "bounce"
    if str(message.get("subject") or "").strip().lower().startswith(BOUNCE_SUBJECTS):
        return "bounce"
    return "reply"


def _predates(date_raw, sent_at: datetime | None) -> bool:
    dt = parse_mail_date(date_raw)
    return sent_at is not None and dt is not None and dt < sent_at - PREDATE_MARGIN


def _thread_ids(sl: dict, deps: MailDeps, cfg: dict) -> list[str]:
    """The threads worth fetching: the watched thread or the subject query's
    threads, plus the bounce query's, because a mailer-daemon report arrives as
    its own thread with its own subject (Codex on GRIP#8633)."""
    watching = sl["mode"] == "thread_watch" and bool(sl.get("thread_id"))
    queries = ([] if watching else [cfg["query"]]) + ([cfg["bounce_query"]] if cfg.get("bounce_query") else [])
    sent_at = parse_iso(sl["sent_at"])
    ids: list[str] = [sl["thread_id"]] if watching else []
    for query in queries:
        for row in deps.search(cfg.get("account", "work"), query, int(cfg.get("max_results", 50))):
            tid = str(row.get("thread_id") or "")
            if tid and tid not in ids and not _predates(row.get("date"), sent_at):
                ids.append(tid)
    return ids


def _gather(sl: dict, deps: MailDeps, cfg: dict) -> tuple[list[dict], dict]:
    """All messages in the threads worth scanning, plus detail for the log."""
    try:
        thread_ids = _thread_ids(sl, deps, cfg)
        messages: list[dict] = []
        for tid in thread_ids:
            messages.extend(deps.thread(cfg.get("account", "work"), tid).get("messages", []))
        return messages, {"threads": len(thread_ids)}
    except deps.errors as exc:
        rows = deps.fallback(sl) if deps.fallback else None
        if rows is None:
            raise
        return rows, {"threads": -1, "fallback": True, "gog": f"{type(exc).__name__}: {str(exc)[:60]}"}


def _failure(sl: dict, exc: BaseException, now: datetime) -> Result:
    reason = f"{type(exc).__name__}: {str(exc)[:80]}"
    sl.setdefault("failing_since", iso(now))
    sl["last_error"] = reason
    changes = []
    day = sast_day(now)
    if sl.get("last_fail_alert_day") != day:
        sl["last_fail_alert_day"] = day
        changes.append(change("mail_failing", NAME, operational=True, since=sl["failing_since"], reason=reason))
    return Result(sl, changes, skipped=f"mail: {reason}")


def _as_change(kind: str, message: dict) -> dict:
    when = parse_mail_date(message.get("date"))
    out = change(kind, NAME,
                 when=when.strftime("%d %b %H:%MZ") if when else "time unknown",
                 subject=strip_re(message.get("subject")),
                 thread_id=str(message.get("thread_id") or ""),
                 message_id=str(message.get("message_id") or ""))
    out["from"] = sender_display(message.get("from"))
    return out


def _scan(messages: list[dict], seen: list[str], selves: set[str], bounce_senders,
          sent_at: datetime | None) -> tuple[list[dict], dict]:
    """New messages become changes (``reply`` / ``bounce``). Own messages and
    messages older than the send are baseline: an old thread that wakes up
    brings its whole history back, and none of it is news (Codex on GRIP#8633).
    ``seen`` is extended in place; the counts carry the own threads too."""
    changes: list[dict] = []
    counts: dict = {"own": 0, "old": 0, "own_threads": set()}
    for message in messages:
        mid = str(message.get("message_id") or "")
        if not mid or mid in seen:
            continue
        seen.append(mid)
        if _predates(message.get("date"), sent_at):
            counts["old"] += 1
            continue
        kind = classify(message, selves, bounce_senders)
        if kind == "self":
            counts["own"] += 1
            counts["own_threads"].add(str(message.get("thread_id") or ""))
            continue
        changes.append(_as_change(kind, message))
    return changes, counts


def collect(slice_: dict, deps: MailDeps, *, now: datetime, config: dict) -> Result:
    cfg = config["mail"]
    sl = dict(slice_)
    sl.setdefault("mode", "search")
    sl.setdefault("sent_at", cfg["sent_at"])
    try:
        messages, detail = _gather(sl, deps, cfg)
    except deps.errors as exc:
        return _failure(sl, exc, now)
    sl.pop("failing_since", None)
    sl.pop("last_error", None)
    selves = {a.lower() for a in deps.self_addresses()} | {a.lower() for a in cfg.get("self_addresses", [])}
    seen = list(sl.get("seen_message_ids", []))
    changes, counts = _scan(messages, seen, selves, tuple(cfg.get("bounce_senders", DEFAULT_BOUNCE_SENDERS)),
                            parse_iso(sl["sent_at"]))
    own_threads = set(sl.get("own_thread_ids", [])) | counts["own_threads"]
    sl["own_thread_ids"] = sorted(t for t in own_threads if t)
    # only a reply in a thread the operator wrote in takes the watch: a reply in some
    # other matching thread is news, but watching it would blind the real one
    first_reply = next((c for c in changes if c["kind"] == "reply" and c["thread_id"] in own_threads), None)
    if first_reply is not None and sl["mode"] != "thread_watch":
        sl.update(mode="thread_watch", thread_id=first_reply["thread_id"])
        first_reply["switched"] = True
    sl["seen_message_ids"] = cap(seen)
    detail.update(messages=len(messages), own=counts["own"], old=counts["old"],
                  replies=sum(1 for c in changes if c["kind"] == "reply"),
                  bounces=sum(1 for c in changes if c["kind"] == "bounce"))
    return Result(sl, changes, None, detail)


def describe(result: Result) -> str:
    if result.skipped:
        return f"skipped: {result.skipped}"
    d = result.detail
    own = int(d.get("own", 0))
    bits = [f"{own} own message{'' if own == 1 else 's'}"]
    if d.get("replies"):
        bits.append(f"{d['replies']} reply already in the thread, not alerted")
    if d.get("bounces"):
        bits.append(f"{d['bounces']} bounce already in the thread, not alerted")
    return ", ".join(bits)


# ---------------------------------------------------------------------------
# Real and fixture I/O
# ---------------------------------------------------------------------------
def default_deps(config: dict) -> MailDeps:
    import grasp_google as gg  # the real gog route; lazy so a test never needs the binary
    cfg = config["mail"]

    def selves() -> set[str]:
        try:
            return {gg.resolve_account(cfg.get("account", "work")).lower()}
        except ValueError:
            return set()

    return MailDeps(search=gg.gmail_search, thread=gg.gmail_thread, self_addresses=selves,
                    fallback=lambda sl: _dwd_fallback(cfg, sl),
                    errors=(gg.GogError, OSError, ValueError))


def _dwd_fallback(cfg: dict, sl: dict) -> list[dict] | None:
    """The service-account route, used only when its key file exists and gog
    failed. Any failure of its own returns None, so the gog error stands."""
    try:
        import meet_absorb_dwd as dwd
    except ImportError:
        return None
    if not dwd.DEFAULT_KEY_PATH.exists():
        return None
    try:
        import grasp_google as gg
        target = gg.resolve_account(cfg.get("account", "work"))
        sent_at = parse_iso(sl.get("sent_at") or cfg["sent_at"])
        hours = max(1, int((datetime.now(timezone.utc) - sent_at).total_seconds() // 3600) + 1)
        rows = dwd.list_gmail_messages(target, hours_back=hours, limit=int(cfg.get("max_results", 50)),
                                       labels=None)
    except Exception:  # noqa: BLE001 -- the fallback failing means the primary error is reported
        return None
    want = str(cfg["subject"]).lower()
    return [{"message_id": r.message_id, "thread_id": r.thread_id, "from": r.sender,
             "subject": r.subject, "date": r.date_utc, "snippet": r.snippet}
            for r in rows if want in (r.subject or "").lower()]


def fixture_deps(directory: Path, selves: frozenset[str] = frozenset({FIXTURE_SELF})) -> MailDeps:
    """Receipts from ``gmail_search.json`` and ``gmail_thread.json`` in ``directory``."""
    directory = Path(directory)

    def search(account: str, query: str, max_results: int) -> list[dict]:
        name = "gmail_bounce_search.json" if "mailer-daemon" in query.lower() else "gmail_search.json"
        path = directory / name
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else []

    def thread(account: str, thread_id: str) -> dict:
        specific = directory / f"gmail_thread_{thread_id}.json"
        path = specific if specific.exists() else directory / "gmail_thread.json"
        return json.loads(path.read_text(encoding="utf-8"))

    return MailDeps(search=search, thread=thread, self_addresses=lambda: set(selves))
