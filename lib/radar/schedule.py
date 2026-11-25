"""When each source runs: pure functions of (source, now, state, config).

The launchd plist fires every 30 minutes. A tick runs only the sources whose
``now - last_run >= interval - 60 s``; the minute of slack absorbs a timer that
fires a few seconds early. A source that was skipped for network keeps its old
``last_run`` (the tick never advances it on a skip), so it retries next tick.

The reply watch decays with the age of the sent mail, as agreed with V>>:
every 30 min for 48 h, hourly to day 7, 4-hourly to day 30, then daily. The
first reply switches it to ``thread_watch``, which runs every 2 h.

The roll-up fires on the first tick of each SAST day at or after 08:00.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

SAST = ZoneInfo("Africa/Johannesburg")
SLACK_S = 60
ROLLUP_HOUR_SAST = 8
HOUR = 3600
DAY = 86400

# Fixed intervals. The reply watch is the exception: its interval decays (below).
INTERVALS_S = {
    "github_repo": 1800,
    "vercel_site": 3600,
    "search_console": 86400,   # once a day, after 08:00 SAST: see due()
    "ietf": 21600,
    "mentions": 14400,
}
DAILY_AFTER_EIGHT = ("search_console",)

# (age ceiling of the sent mail in seconds, interval while under that ceiling)
MAIL_LADDER = ((48 * HOUR, 1800), (7 * DAY, HOUR), (30 * DAY, 4 * HOUR))
MAIL_INTERVAL_AFTER_LADDER = DAY
THREAD_WATCH_INTERVAL_S = 2 * HOUR


def parse_iso(value) -> datetime | None:
    """An aware UTC datetime from an ISO string (``Z`` accepted), else None."""
    if not isinstance(value, str) or not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sast(now: datetime) -> datetime:
    return now.astimezone(SAST)


def sast_day(now: datetime) -> str:
    return sast(now).date().isoformat()


def mail_interval(now: datetime, sent_at: datetime, mode: str = "search") -> int:
    if mode == "thread_watch":
        return THREAD_WATCH_INTERVAL_S
    age = (now - sent_at).total_seconds()
    for ceiling, interval in MAIL_LADDER:
        if age < ceiling:
            return interval
    return MAIL_INTERVAL_AFTER_LADDER


def interval_for(source: str, now: datetime, state: dict, config: dict) -> int:
    if source == "mail_replies":
        sl = state.get("sources", {}).get(source, {})
        sent_at = parse_iso(sl.get("sent_at")) or parse_iso(config["mail"]["sent_at"])
        return mail_interval(now, sent_at, sl.get("mode", "search"))
    try:
        return INTERVALS_S[source]
    except KeyError:  # a source with no interval is a wiring error: say which, and where it goes
        raise KeyError(f"radar source {source!r} has no interval in schedule.INTERVALS_S; "
                       "add it where the source is registered") from None


def last_run(source: str, state: dict) -> datetime | None:
    return parse_iso(state.get("sources", {}).get(source, {}).get("last_run"))


def due(source: str, now: datetime, state: dict, config: dict) -> bool:
    if source in DAILY_AFTER_EIGHT and sast(now).hour < ROLLUP_HOUR_SAST:
        return False  # the API lags a day or two; read the figures once, after 08:00 SAST
    last = last_run(source, state)
    if last is None:
        return True
    return (now - last).total_seconds() >= interval_for(source, now, state, config) - SLACK_S


def next_due(source: str, now: datetime, state: dict, config: dict) -> datetime:
    last = last_run(source, state)
    if last is None:
        return now
    return last + timedelta(seconds=interval_for(source, now, state, config))


def rollup_due(now: datetime, state: dict) -> bool:
    local = sast(now)
    if local.hour < ROLLUP_HOUR_SAST:
        return False
    return local.date().isoformat() != (state.get("rollup") or {}).get("last_day")
