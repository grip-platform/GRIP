"""What led up to each Claude usage cap, read from HAL's request history.

HAL's wire writes one row per ``/v1/messages`` answer to
``$HAL_HOME/logs/cache-loss-ledger.jsonl`` (``lib/hal/cache_loss.py`` in HAL). A row
holds the time, the session, the account label, the organisation that answered, the
model, the token counts, and the account's five-hour and weekly use after that
answer. It never holds a prompt or a token.

A cap is the first row whose window reads 100% or more after a row, for the same
organisation, that read less. Rows are grouped by organisation, not by label:
accounts in one organisation share their limits, and one organisation can reach the
wire under several labels (a pooled token, and a signed-in session that HAL files as
``unpooled``). For each cap this names the sessions that used that organisation in
its lead-up, with their requests, tokens by type and models, and the labels it
reached the wire under in that time.

Readings jump without the window renewing: answers to concurrent sessions arrive out
of order, and answers from different models can carry different figures. So a window
is taken to have renewed only when a reading falls to 50% or below by 25 points or
more (measured below, at :data:`RENEWED_PERCENT`). A capped window counts again after
such a renewal, or once its whole length has gone by since the cap. A lead-up covers
the window's length before the cap, and starts at a renewal seen inside it. And it
reports facts, not shares: summing each session's rise in the reading would count
every jump again.

What it cannot see: use that did not pass through this machine's wire (the relay
host, claude.ai, the phone), and anything before the history's first row. HAL keeps
about 20 MiB of it, in two files; a lead-up that would reach back past the first row
says it is only part of the window. A reading from longer ago than the window's
length says nothing about the window now, so a cap with no reading under it in that
time is reported as such, with no lead-up. Times are shown in UTC. A usage value is
read with the relay's rule (``subscription_room.percent_used``). Read-only: it reads
HAL's two history files and reaches no network.
"""
from __future__ import annotations

import json
import math
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import subscription_room as room

#: The two account-wide windows: the field HAL writes, the window's length, its name.
WINDOWS = {
    "five_hour": ("five_hour_utilization", timedelta(hours=5), "five-hour window"),
    "seven_day": ("seven_day_utilization", timedelta(days=7), "week"),
}
CAP_PERCENT = 100.0
#: A window is seen to renew when a reading falls to :data:`RENEWED_PERCENT` or below,
#: by :data:`RENEWAL_FALL` points or more. Readings jump without a renewal: answers to
#: concurrent sessions arrive out of order, and answers from different models can
#: carry different figures. Measured on this Mac's history (2026-10-08 to 2026-10-15),
#: 94% of consecutive five-hour readings agreed within two points, falls of 10 to 20
#: points came and went within minutes, and every renewal fell from 63-101% to 0-22%.
RENEWED_PERCENT = 50.0
RENEWAL_FALL = 25.0
#: Sessions named under each cap; the others are summed on one line.
SHOWN_SESSIONS = 3
#: Characters of an organisation id that are ever shown. grip-relay-bill's rule
#: allows at most eight, and never more than half of the id.
ORG_TAG = 4
#: HAL's label for a request whose token is not one of the pooled tokens.
UNPOOLED = "unpooled"
#: The label given here to a row that carries none. It is not a shape a stored
#: account's label can take (``claude_token_store.LABEL_SHAPE``), so it never
#: collides with a real one.
UNLABELLED = "(no label)"
#: Times both history files are read before the reading is taken as it stands, when
#: HAL moves the newer file aside during a read.
READ_ATTEMPTS = 3


@dataclass(frozen=True)
class Row:
    """One answer in HAL's history; ``readings`` maps each window to a percentage, or
    to None when the row holds no reading for it."""
    when: datetime
    org: str
    label: str
    session: str
    model: str
    usage: dict
    readings: dict


@dataclass(frozen=True)
class Cap:
    """One window reaching its cap. ``label`` is the capping answer's label.
    ``before`` is the reading under the cap that it rose from, or None when no such
    reading was seen in the window's length before it. ``since`` is the last reading
    before the window's latest seen renewal, after which its lead-up starts, or None."""
    org: str
    label: str
    window: str
    at: datetime
    before: float | None
    reading: float
    since: datetime | None = None


@dataclass
class SessionUse:
    """What one session sent on one organisation in a cap's lead-up."""
    session: str
    requests: int = 0
    input: int = 0
    cache_writes: int = 0
    cache_reads: int = 0
    output: int = 0
    models: Counter = field(default_factory=Counter)


def history_path() -> Path:
    """HAL's request history, under HAL's home."""
    return room.hal_home() / "logs" / "cache-loss-ledger.jsonl"


def history_paths() -> list:
    """HAL's request history, older file first. When the file reaches HAL's size cap
    HAL moves it to ``.1`` and starts a new one (``_rotate_if_big`` in HAL's
    ``lib/hal/cache_loss.py``), so a lead-up can begin in the older file."""
    current = history_path()
    return [current.with_name(current.name + ".1"), current]


def _text(value, default: str) -> str:
    return value if isinstance(value, str) and value else default


def _when(value) -> datetime | None:
    """A row's time in UTC, or None unless it is an ISO time with a zone. HAL writes
    UTC; another zone is converted, so a time shown as UTC is one."""
    if not isinstance(value, str):
        return None
    try:
        when = datetime.fromisoformat(value)
    except ValueError:
        return None
    return when.astimezone(timezone.utc) if when.tzinfo is not None else None


def _row(doc) -> Row | None:
    """A history line as a :class:`Row`, or None when it has no time or organisation."""
    if not isinstance(doc, dict):
        return None
    when, org = _when(doc.get("ts")), doc.get("org_id")
    if when is None or not isinstance(org, str) or not org:
        return None
    usage = doc.get("usage")
    return Row(when=when, org=org, label=_text(doc.get("account_label"), UNLABELLED),
               session=_text(doc.get("session_id"), "unknown"),
               model=_text(doc.get("model"), "unknown"),
               usage=usage if isinstance(usage, dict) else {},
               readings={name: _hairless(room.percent_used(doc.get(spec[0])))
                         for name, spec in WINDOWS.items()})


def _hairless(percent: float | None) -> float | None:
    """A percentage with the float hair of the fraction-to-percent step removed, so a
    rule at a boundary sees the figure HAL wrote: 0.80 * 100 - 0.55 * 100 is
    24.999999999999993, not 25."""
    return round(percent, 6) if percent is not None else None


def _rows_in(path: Path) -> list:
    """The rows of one history file. A missing or unreadable file, or a line that is
    not a row, gives nothing."""
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    rows = []
    for line in lines:
        try:
            row = _row(json.loads(line))
        except ValueError:
            continue
        if row is not None:
            rows.append(row)
    return rows


def _identity(path: Path) -> tuple | None:
    """Which file is at ``path`` and how big it is, or None when there is none."""
    try:
        stat = path.stat()
    except OSError:
        return None
    return stat.st_ino, stat.st_size, stat.st_mtime_ns


def _both_files() -> list:
    """The rows of both history files, read as one pair. HAL can move the newer file
    onto the older between the two reads, which would skip a whole file: so the older
    file is identified before and after, and the pair is read again when it changed,
    up to :data:`READ_ATTEMPTS` times."""
    older, newer = history_paths()
    rows: list = []
    for _attempt in range(READ_ATTEMPTS):
        before = _identity(older)
        rows = _rows_in(older) + _rows_in(newer)
        if _identity(older) == before:
            break
    return rows


def read_rows(path: Path | None = None) -> list:
    """Every row of HAL's history that has a time and an organisation, oldest first:
    the one file at ``path``, or both of HAL's files (:func:`history_paths`)."""
    rows = _rows_in(path) if path is not None else _both_files()
    return sorted(rows, key=lambda row: row.when)


@dataclass(frozen=True)
class _Track:
    """What :func:`caps` remembers of one window of one organisation: its last
    reading and when, when it last reached its cap without renewing since, and the
    last reading before its latest seen renewal."""
    reading: float
    when: datetime
    capped_at: datetime | None
    since: datetime | None


def _renewed(track: _Track, reading: float) -> bool:
    """Whether this reading shows the window renewed after the last one: it fell to
    :data:`RENEWED_PERCENT` or below, by :data:`RENEWAL_FALL` points or more."""
    return reading <= RENEWED_PERCENT and track.reading - reading >= RENEWAL_FALL


def _step(track: _Track | None, row: Row, window: str, reading: float) -> tuple:
    """The cap one reading makes, if any, and the window's track after it. A reading
    from longer ago than the window's length says nothing about this one. A capped
    window counts again once it has renewed, or once its whole length has gone by
    since the cap. A cap rises from a reading under it: when the reading before was
    over the cap too, the rise was not seen."""
    length = WINDOWS[window][1]
    if track is not None and row.when - track.when >= length:
        track = None
    renewed = track is not None and _renewed(track, reading)
    since = track.when if renewed else (track.since if track is not None else None)
    capped_at = None if track is None or renewed else track.capped_at
    if capped_at is not None and row.when - capped_at >= length:
        capped_at = None
    before = track.reading if track is not None and track.reading < CAP_PERCENT else None
    cap = None
    if reading >= CAP_PERCENT and capped_at is None:
        cap = Cap(row.org, row.label, window, row.when, before, reading, since)
        capped_at = row.when
    return cap, _Track(reading, row.when, capped_at, since)


def caps(rows: list) -> list:
    """Each moment a window reached its cap, in the order of ``rows`` (oldest first
    from :func:`read_rows`). Only rows that hold a reading count, so a row with none
    neither starts nor ends a cap."""
    tracks: dict = {}
    found = []
    for row in rows:
        for window, reading in row.readings.items():  # O(1): two windows per row
            if reading is None:
                continue
            cap, tracks[(window, row.org)] = _step(tracks.get((window, row.org)), row,
                                                   window, reading)
            if cap is not None:
                found.append(cap)
    return found


def _count(value) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0


def _add(use: SessionUse, row: Row) -> None:
    use.requests += 1
    use.input += _count(row.usage.get("input_tokens"))
    use.cache_writes += _count(row.usage.get("cache_creation_tokens"))
    use.cache_reads += _count(row.usage.get("cache_read_tokens"))
    use.output += _count(row.usage.get("output_tokens"))
    use.models[row.model] += 1


def lead_up_start(cap: Cap) -> datetime:
    """Where the cap's lead-up starts, not included: the window's length before the
    cap, or the window's latest seen renewal when that is later."""
    start = cap.at - WINDOWS[cap.window][1]
    return max(start, cap.since) if cap.since is not None else start


def _lead_up_rows(rows: list, cap: Cap) -> list:
    """The cap's organisation's rows in its lead-up, the answer that reached the cap
    included."""
    start = lead_up_start(cap)
    return [row for row in rows if row.org == cap.org and start < row.when <= cap.at]


def lead_up(rows: list, cap: Cap) -> list:
    """The sessions that used the cap's organisation in the window's length before the
    cap, the answer that reached it included, the most output first."""
    uses: dict = {}
    for row in _lead_up_rows(rows, cap):
        _add(uses.setdefault(row.session, SessionUse(row.session)), row)
    return sorted(uses.values(), key=lambda use: (-use.output, -use.requests, use.session))


def cap_labels(rows: list, cap: Cap) -> list:
    """The labels the account reached the wire under in the cap's lead-up, so a label
    first used after the cap is never named for it."""
    if cap.before is None:
        return [cap.label]
    return sorted({row.label for row in _lead_up_rows(rows, cap)} | {cap.label})


def org_tag(org: str) -> str:
    """At most :data:`ORG_TAG` characters of an organisation id, and never more than
    half of it."""
    return org[:ORG_TAG] if len(org) >= 2 * ORG_TAG else "?"


def _iso(when: datetime) -> str:
    return when.isoformat(timespec="seconds")


def _use_view(use: SessionUse) -> dict:
    return {"session": use.session, "requests": use.requests, "input": use.input,
            "cache_writes": use.cache_writes, "cache_reads": use.cache_reads,
            "output": use.output, "models": dict(use.models.most_common())}


def _lead_up_bound(rows: list, cap: Cap) -> tuple:
    """Where the lead-up shown starts, and what set it: the window's length
    (``window``), a renewal seen inside it (``renewal``), or the start of HAL's
    history when that is later (``history``: only part of the window is there)."""
    start = lead_up_start(cap)
    bound = "renewal" if start != cap.at - WINDOWS[cap.window][1] else "window"
    first = rows[0].when if rows else cap.at
    return (first, "history") if first > start else (start, bound)


def _cap_view(rows: list, cap: Cap) -> dict:
    known = cap.before is not None
    view = {"labels": cap_labels(rows, cap), "organisation": org_tag(cap.org),
            "window": cap.window, "at": _iso(cap.at), "before_percent": cap.before,
            "reading_percent": cap.reading, "lead_up_known": known,
            "lead_up_hours": WINDOWS[cap.window][1] / timedelta(hours=1),
            "sessions": [_use_view(use) for use in lead_up(rows, cap)] if known else []}
    if known:
        start, bound = _lead_up_bound(rows, cap)
        view.update(lead_up_from=_iso(start), lead_up_bound=bound)
    return view


def blackbox(rows: list, last: int = 5) -> dict:
    """The newest ``last`` caps (at least one), newest first, each with its lead-up,
    and the span of the history they were read from."""
    found = caps(rows)[-max(last, 1):]
    return {"history": {"rows": len(rows),
                        "first": _iso(rows[0].when) if rows else None,
                        "last": _iso(rows[-1].when) if rows else None},
            "caps": [_cap_view(rows, cap) for cap in reversed(found)]}


def _percent(value: float) -> str:
    """A reading as a whole percentage, cut rather than rounded. The float hair is
    removed first (0.29 * 100 is 28.999999999999996), and a reading under the cap
    never shows as 100% however close to it it is."""
    shown = math.floor(round(value, 9))
    if value < CAP_PERCENT:
        shown = min(shown, int(CAP_PERCENT) - 1)
    return f"{shown}%"


def _size(count: int) -> str:
    if count >= 1_000_000:
        return f"{count / 1_000_000:.1f}M"
    if count >= 1_000:
        return f"{count / 1_000:.0f}k"
    return str(count)


def _short(stamp: str) -> str:
    """``2026-10-12T03:17:24+00:00`` as ``2026-10-12 03:17``."""
    return stamp[:16].replace("T", " ")


def _account(cap: dict) -> str:
    """The account's name for the record: its pooled labels, or what it is when it
    has none. ``unpooled`` and a missing label are never shown as a pooled name."""
    labels = cap["labels"]
    pooled = [label for label in labels if label not in (UNPOOLED, UNLABELLED)]
    if pooled:
        name = ", ".join(pooled)
        if UNPOOLED in labels:
            name += " (also reached outside the pool)"
    elif UNPOOLED in labels:
        name = "an account outside the pool"
    else:
        name = "an account HAL did not label"
    return f"{name}, organisation {cap['organisation']}"


def _short_ids(sessions: list) -> dict:
    """Each session's shortest prefix, of at least eight characters, that no other
    session in ``sessions`` shares. Eight is how GRIP names a session elsewhere, and
    it tells two UUIDs apart; two ids that share it get a longer one."""
    ids = [use["session"] for use in sessions]
    width, longest = 8, max((len(session) for session in ids), default=0)
    while width < longest and len({session[:width] for session in ids}) < len(ids):
        width += 4
    return {session: session[:width] for session in ids}


def _session_line(use: dict, short: str) -> str:
    models = use["models"]
    model = next(iter(models)) if models else "unknown"
    lead = "mostly " if len(models) > 1 else ""
    return (f"    {short:<8}  {use['requests']:>6,} requests  "
            f"{_size(use['output']):>6} out  {_size(use['cache_writes']):>6} cache writes  "
            f"{_size(use['cache_reads']):>6} cache reads  {_size(use['input']):>6} in  "
            f"{lead}{model}")


def _span(hours: float) -> str:
    """A lead-up's length: ``5 hours``, or ``7 days`` from two days up."""
    return f"{hours / 24:g} days" if hours >= 48 else f"{hours:g} hours"


def _lead_up_phrase(cap: dict) -> str:
    """How far back the lead-up shown reaches, and why it stops there."""
    if cap["lead_up_bound"] == "renewal":
        return f"Since the window renewed, after {_short(cap['lead_up_from'])} UTC"
    if cap["lead_up_bound"] == "history":
        return (f"In the part of the {_span(cap['lead_up_hours'])} before that HAL's "
                f"history holds, from {_short(cap['lead_up_from'])} UTC")
    return f"In the {_span(cap['lead_up_hours'])} before"


def _render_lead_up(cap: dict) -> list:
    sessions = cap["sessions"]
    total = sum(use["requests"] for use in sessions)
    lines = [f"  {_lead_up_phrase(cap)}, this machine's wire sent {total:,} requests on "
             f"it, from {len(sessions)} sessions:"]
    shown = sessions[:SHOWN_SESSIONS]
    short = _short_ids(shown)
    lines += [_session_line(use, short[use["session"]]) for use in shown]
    rest = sessions[SHOWN_SESSIONS:]
    if rest:
        lines.append(f"    and {len(rest)} more sessions with "
                     f"{sum(use['requests'] for use in rest):,} requests")
    return lines


def _render_cap(cap: dict) -> list:
    window = WINDOWS[cap["window"]][2]
    head = f"{_account(cap)}: the {window} "
    if not cap["lead_up_known"]:
        return [head + f"was already at {_percent(cap['reading_percent'])} at "
                f"{_short(cap['at'])} UTC, with no reading under its cap in the "
                f"{_span(cap['lead_up_hours'])} before it, so what led up to it is not in "
                "the history."]
    return [head + f"reached {_percent(cap['reading_percent'])} at {_short(cap['at'])} UTC, "
            f"up from {_percent(cap['before_percent'])}."] + _render_lead_up(cap)


def render(view: dict) -> list:
    """The record as plain lines for a terminal."""
    span = view["history"]
    if not span["rows"]:
        return ["HAL's request history is empty or missing, so there is no cap to explain.",
                f"It is read from {history_path()}, and the older .1 file beside it."]
    lines = ["What led up to each cap, from HAL's request history on this machine "
             f"({_short(span['first'])} to {_short(span['last'])} UTC, newest first)", ""]
    if not view["caps"]:
        lines += ["No window reached its cap in that time.", ""]
    for cap in view["caps"]:
        lines += _render_cap(cap) + [""]
    lines.append("Use that did not pass through this machine's wire (the relay host, "
                 "claude.ai, the phone) is not counted.")
    return lines
