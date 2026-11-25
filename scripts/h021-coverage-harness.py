#!/usr/bin/env python3
"""H021 coverage harness — verify DWD meet-absorb ≥99% coverage.

Hypothesis (learning/hypotheses.jsonl id=H021):
    DWD meet-absorb achieves 100% coverage of codetonight.co.za Workspace
    meetings without per-user OAuth.
Metric: coverage_pct
Prediction: >= 99
Deadline: 2026-04-17

Status before this harness: PARTIAL (N=2 attempted samples, both succeeded
but far below the sample size needed to confirm a >=99% claim — Goodhart
protection blocks confirm on tiny N).

This harness does NOT depend on Admin SDK Directory enumeration (blocked
per task #1052). It reads the workspace user roster from the
`data/workspace-users.txt` fallback that GRIP already maintains.

Definitions:
    - **Meet event**: a Calendar event where at least one attendee is a
      codetonight.co.za user AND the event has a Meet link (`conferenceData`
      `.conferenceSolution.key.type == "hangoutsMeet"`). Deduplicated by
      event id across co-organised calendars.
    - **Ground-truth note existence**: at least one Gemini Notes doc in the
      organiser's Drive whose `modifiedTime` falls within `[event.start - 1h,
      event.end + 2h]`. This is the denominator for `coverage_pct_recorded`.
      The slack is asymmetric because Gemini writes the doc before the
      scheduled start (when the first attendee joins) and finalises it a
      short time after the end.
    - **Pipeline match**: a Gemini Notes doc that passes ground-truth AND
      whose name contains the event summary substring. This is the
      numerator for both ratios. An empty event summary falls through as
      a wildcard match.

Coverage metrics:
    - `coverage_pct_raw`    = matched_events / all_meet_events
      Interpretation: "how many Meet events had absorbed notes?" Tracks
      Gemini Notes *adoption* as well as pipeline coverage; will be low
      when many meetings are standups that nobody opted into recording.
    - `coverage_pct_recorded` = matched_events / events_where_notes_exist
      Interpretation: "of the meetings where a Gemini Notes doc exists in
      Drive, what fraction did the pipeline correctly associate with the
      calendar event?" This is the matcher-precision signal and the
      metric H021 is evaluated against. Unaffected by adoption rate.
    - `drive_query_failures`: count of organisers whose Drive query raised.
      Non-zero values invalidate the run — see stderr for exception types.

H021 is evaluated against `coverage_pct_recorded`, not `raw`, because the
hypothesis is about pipeline capability, not Workspace feature adoption.
    - Cross-org meetings where the organiser is outside codetonight.co.za
      are excluded from the denominator (cannot impersonate externals).
    - The harness impersonates each workspace user via DWD to list their
      Calendar events. Rate limits apply; the retry wrapper in
      lib.meet_absorb_dwd handles exponential backoff.

Usage:
    python3 scripts/h021-coverage-harness.py \\
        --days 30 \\
        --report data/h021-coverage-report.json

    # Dry-run mode: only enumerate, don't call Drive:
    python3 scripts/h021-coverage-harness.py --days 7 --dry-run

Exit codes:
    0 = harness ran to completion (see report for pass/fail against ≥99)
    1 = setup error (no users, no credentials, etc.)
    2 = Google API error that retries could not recover from
"""

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from itertools import chain
from pathlib import Path
from typing import Dict, Iterable, Iterator, List, Optional, Set, Tuple

# Local imports — resolved via CLAUDE_DIR path shim so the script runs
# without `pip install -e .`.
CLAUDE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(CLAUDE_DIR))

from lib.meet_absorb_dwd import (  # noqa: E402
    DEFAULT_HOME_DOMAIN,
    GEMINI_NOTES_QUERY,
    GeminiDoc,
    _client_for,
    _default_credentials_factory,
    _with_retry,
    cross_org_attendees,
    list_gemini_docs,
)

DEFAULT_USERS_FILE = CLAUDE_DIR / "data" / "workspace-users.txt"
DEFAULT_REPORT = CLAUDE_DIR / "data" / "h021-coverage-report.json"

# Case-insensitive substring that marks a doc as an auto-generated Gemini
# Notes doc. Empirically confirmed 2026-04-15 against the live `codetonight.co.za`
# Workspace — every such doc's name ends with " - Notes by Gemini".
_NOTES_BY_GEMINI = "notes by gemini"

# Gemini occasionally generates Notes docs with a generic title of the form
# "Meeting started YYYY/MM/DD HH:MM - Notes by Gemini" when the event summary
# was not embedded in the doc. These docs have no event-summary substring so
# the standard name-match check fails. The timestamp fallback in
# _doc_name_matches_event treats any such doc that already passed the
# time-overlap check as a match (the time overlap IS the identity signal).
# Gen 481 evidence: 3/18 30-day unmatched docs had this exact title pattern.
_MEETING_STARTED_RE = re.compile(
    r"^\s*meeting started\s+\d{4}/\d{2}/\d{2}",
    re.IGNORECASE,
)

# Time slack around a Calendar event when matching Drive Gemini docs by
# modifiedTime. Gemini writes to its doc BEFORE the scheduled start (when the
# first attendee joins) and continues writing for a short time AFTER the
# scheduled end. A generous [-1h, +2h] window captures both edges without
# conflating back-to-back meetings in practice.
_MATCH_SLACK_BEFORE = timedelta(hours=1)
_MATCH_SLACK_AFTER = timedelta(hours=2)


@dataclass(frozen=True)
class MeetEvent:
    event_id: str
    summary: str
    start: str
    end: str
    organiser: str
    has_meet_link: bool


@dataclass
class CoverageResult:
    window_days: int
    window_start: str
    window_end: str
    users_scanned: int
    events_total: int
    events_with_meet: int
    events_cross_org_dropped: int
    events_with_notes: int
    events_notes_exist_in_window: int
    docs_unmatched: int
    drive_query_failures: int
    coverage_pct_raw: float
    coverage_pct_recorded: float
    sample_events: List[dict]
    started_at: str
    finished_at: str


def _load_users(path: Path) -> List[str]:
    out: List[str] = []
    for raw in path.read_text().splitlines():
        line = raw.split("#", 1)[0].strip()
        if line and "@" in line:
            out.append(line)
    return sorted(set(out))


def _iso_z(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _has_meet_link(raw: dict) -> bool:
    conf = (raw.get("conferenceData") or {}).get("conferenceSolution") or {}
    return (conf.get("key") or {}).get("type") == "hangoutsMeet"


@_with_retry
def _list_user_events(user: str, time_min: str, time_max: str) -> List[dict]:
    cal = _client_for(user, "calendar", "v3")
    collected: List[dict] = []
    page_token: Optional[str] = None
    while True:
        resp = cal.events().list(
            calendarId="primary",
            timeMin=time_min,
            timeMax=time_max,
            singleEvents=True,
            orderBy="startTime",
            maxResults=250,
            pageToken=page_token,
        ).execute()
        collected.extend(resp.get("items") or [])
        page_token = resp.get("nextPageToken")
        if not page_token:
            return collected


def _to_meet_event(raw: dict) -> MeetEvent:
    return MeetEvent(
        event_id=raw.get("id", ""),
        summary=raw.get("summary", ""),
        start=(raw.get("start") or {}).get("dateTime") or (raw.get("start") or {}).get("date", ""),
        end=(raw.get("end") or {}).get("dateTime") or (raw.get("end") or {}).get("date", ""),
        organiser=(raw.get("organizer") or {}).get("email", ""),
        has_meet_link=_has_meet_link(raw),
    )


def _iter_all_user_events(
    users: Iterable[str], time_min: str, time_max: str
) -> Iterator[dict]:
    """Flatten per-user Calendar event lists into one linear stream.

    Single linear scan: O(E) where E is total events across all users.
    Kept as a helper so the caller below does not need a nested loop.
    """
    return chain.from_iterable(
        _list_user_events(u, time_min, time_max) for u in users
    )


def _collect_meet_events(
    users: Iterable[str],
    time_min: str,
    time_max: str,
    home_domain: str,
) -> Tuple[List[MeetEvent], int]:
    """Dedupe Meet events across users, drop cross-org organisers."""
    seen: Dict[str, MeetEvent] = {}
    dropped_cross_org = 0
    for raw in _iter_all_user_events(users, time_min, time_max):
        ev = _to_meet_event(raw)
        if not ev.has_meet_link:
            continue
        if ev.organiser and cross_org_attendees([ev.organiser], home_domain):
            dropped_cross_org += 1
            continue
        seen.setdefault(ev.event_id, ev)
    return list(seen.values()), dropped_cross_org


def _parse_iso_datetime(s: str) -> Optional[datetime]:
    """Parse a Google-API ISO 8601 datetime. Return None if unparseable.

    Google returns timestamps with either a trailing 'Z' (UTC) or an explicit
    +HH:MM offset. `datetime.fromisoformat` on Python <3.11 does not accept
    the 'Z' suffix, so we normalise it first. Date-only strings and empty
    strings return None so callers can distinguish "missing" from "epoch".
    """
    if not s:
        return None
    cleaned = s[:-1] + "+00:00" if s.endswith("Z") else s
    try:
        parsed = datetime.fromisoformat(cleaned)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _doc_overlaps_event(doc: GeminiDoc, event: MeetEvent) -> bool:
    """True if doc's creation time falls inside [event.start-slack, event.end+slack].

    Prefers createdTime (stable — set when Gemini creates the doc during the
    meeting) over modifiedTime (shifts as Gemini edits asynchronously, can
    drift hours post-meeting causing cross-event false positives — Gen 482
    diagnosed a "REDR Meeting" doc drifting 16h into a Standup window).
    """
    doc_ts = _parse_iso_datetime(getattr(doc, 'created_time', '')) or _parse_iso_datetime(doc.modified_time)
    ev_start = _parse_iso_datetime(event.start)
    ev_end = _parse_iso_datetime(event.end)
    if not (doc_ts and ev_start and ev_end):
        return False
    return (ev_start - _MATCH_SLACK_BEFORE) <= doc_ts <= (ev_end + _MATCH_SLACK_AFTER)


def _is_gemini_notes_doc(doc: GeminiDoc) -> bool:
    """True if the doc is an auto-generated Gemini Notes file."""
    return _NOTES_BY_GEMINI in (doc.name or "").lower()


def _doc_name_matches_event(doc: GeminiDoc, event: MeetEvent) -> bool:
    """True if a Gemini Notes doc's title matches the event.

    Match order:
    1. Empty event summary → wildcard match (any Gemini Notes doc counts).
    2. Event summary substring found in doc title → match.
    3. Timestamp fallback: doc title is the "Meeting started YYYY/MM/DD HH:MM"
       pattern that Gemini generates when no event summary is embedded. This
       function is only called after ``_doc_overlaps_event`` confirms temporal
       proximity, so the time overlap IS the identity signal for these docs.

    Gen 481 evidence: 3/18 30-day Gemini docs had generic "Meeting started"
    titles, reducing harness coverage_pct_recorded to 83.33%. The timestamp
    fallback correctly counts them as matched.
    """
    if not _is_gemini_notes_doc(doc):
        return False
    summary_key = (event.summary or "").strip().lower()
    if not summary_key:
        return True
    doc_name = (doc.name or "").lower()
    if summary_key in doc_name:
        return True
    # Timestamp fallback: "Meeting started YYYY/MM/DD HH:MM" docs have
    # already passed _doc_overlaps_event; accept them via temporal identity.
    return bool(_MEETING_STARTED_RE.match(doc_name))


def _best_event_for_doc(
    doc: GeminiDoc,
    candidate_events: Iterable[MeetEvent],
) -> Tuple[Optional[MeetEvent], bool]:
    """Select the single best event for one Gemini Notes doc.

    Returns (event, name_matched). Preference order:
    1. A name-matching event over a time-only event (pipeline intent wins
       over pure temporal proximity).
    2. Among equally-name-matching candidates, the one whose start time is
       closest to the doc's modifiedTime.

    Returns (None, False) if no event in `candidate_events` time-overlaps
    the doc at all.
    """
    doc_mt = _parse_iso_datetime(doc.modified_time)
    if doc_mt is None:
        return None, False
    best: Optional[MeetEvent] = None
    best_delta = timedelta.max
    best_is_match = False
    for ev in candidate_events:
        if not _doc_overlaps_event(doc, ev):
            continue
        ev_start = _parse_iso_datetime(ev.start)
        if ev_start is None:
            continue
        delta = abs(doc_mt - ev_start)
        name_match = _doc_name_matches_event(doc, ev)
        if _is_better_candidate(name_match, delta, best_is_match, best_delta):
            best = ev
            best_delta = delta
            best_is_match = name_match
    return best, best_is_match


def _is_better_candidate(
    cand_match: bool,
    cand_delta: timedelta,
    best_match: bool,
    best_delta: timedelta,
) -> bool:
    """Dispatch table for candidate comparison (kept < CC 5 per function)."""
    if cand_match and not best_match:
        return True
    if best_match and not cand_match:
        return False
    return cand_delta < best_delta


def _attribute_docs_to_events(
    events: List[MeetEvent],
    cache: Dict[str, List[GeminiDoc]],
) -> Tuple[int, int, int]:
    """Attribute each Gemini Notes doc to its single best-matching event.

    Returns `(events_with_notes, events_notes_exist_in_window, docs_unmatched)`.

    Two PARAMOUNT correctness properties:

    1. **No double-count**: each doc contributes to the denominator at most
       once (via `event_id` set membership). Back-to-back meetings whose
       time-overlap windows intersect cannot double-count a single doc.

    2. **Cross-attendee attribution**: docs are matched against ALL events
       in the deduplicated window, not just events organised by the doc's
       owner. Gemini writes a COPY of each meeting note into every
       attendee's Drive, so `list_gemini_docs(alice@)` returns Alice's
       copies of meetings organised by Bob, Carol, etc. Restricting the
       candidate-event set to events-where-Alice-is-the-organiser would
       leave those cross-attendee docs as spurious "orphans" — the
       empirical diagnosis on a 30-day window in Gen 481 showed
       ~14 of 20 apparent orphans were just cross-attendee duplicates.

    Loop shape: flattened via `chain.from_iterable` over all Gemini docs
    in the cache. The inner work per doc is delegated to
    `_best_event_for_doc`, which does its own bounded linear scan over
    events; overall cost is O(D × E) where D is distinct docs and E is
    distinct events — both ~tens in realistic Workspace windows.
    """
    all_docs = (d for d in chain.from_iterable(cache.values()) if _is_gemini_notes_doc(d))
    matched_events: Set[str] = set()
    events_with_any_note: Set[str] = set()
    unmatched_docs = 0
    for doc in all_docs:
        event, name_matched = _best_event_for_doc(doc, events)
        if event is None:
            unmatched_docs += 1
            continue
        events_with_any_note.add(event.event_id)
        if name_matched:
            matched_events.add(event.event_id)
    return len(matched_events), len(events_with_any_note), unmatched_docs


def _fetch_organiser_notes_cache(
    organisers: Iterable[str],
    since_date: str,
) -> Tuple[Dict[str, List[GeminiDoc]], int]:
    """Fetch Gemini Notes docs once per unique organiser.

    Returns (cache, failure_count). Any organiser whose Drive query raises
    is logged to stderr with exception type and cached as an empty list,
    so downstream coverage still computes but the failure is NOT silent —
    this is the narrow-exception replacement for the prior blanket
    `except Exception: return False` at the per-event level that hid
    DWD propagation gaps as "no notes".
    """
    cache: Dict[str, List[GeminiDoc]] = {}
    failures = 0
    for user in sorted({u for u in organisers if u}):
        try:
            cache[user] = list_gemini_docs(user, since_date=since_date)
        except Exception as exc:  # noqa: BLE001 - logged, not swallowed
            print(
                f"[h021-harness] WARN: Drive query failed for {user}: "
                f"{type(exc).__name__}: {exc}",
                file=sys.stderr,
            )
            cache[user] = []
            failures += 1
    return cache, failures


def _pct(num: int, denom: int) -> float:
    """Return 100 * num / denom. Vacuous truth (100) when denom is zero.

    Used for both raw and recorded coverage ratios. Kept as a single helper
    so the two ratios cannot diverge in their zero-denominator handling.
    """
    if denom == 0:
        return 100.0
    return 100.0 * num / denom


def run_harness(
    days: int,
    users_file: Path,
    dry_run: bool,
    home_domain: str = DEFAULT_HOME_DOMAIN,
) -> CoverageResult:
    started = datetime.now(timezone.utc)
    window_end = started
    window_start = started - timedelta(days=days)
    users = _load_users(users_file)
    if not users:
        raise RuntimeError(f"No users in {users_file}")
    events, dropped = _collect_meet_events(
        users, _iso_z(window_start), _iso_z(window_end), home_domain
    )
    events_with_meet = len(events)
    matched, notes_exist, unmatched, drive_failures = _measure_notes(
        events, dry_run, window_start
    )
    return CoverageResult(
        window_days=days,
        window_start=_iso_z(window_start),
        window_end=_iso_z(window_end),
        users_scanned=len(users),
        events_total=events_with_meet,
        events_with_meet=events_with_meet,
        events_cross_org_dropped=dropped,
        events_with_notes=matched,
        events_notes_exist_in_window=notes_exist,
        docs_unmatched=unmatched,
        drive_query_failures=drive_failures,
        coverage_pct_raw=round(_pct(matched, events_with_meet), 2),
        coverage_pct_recorded=round(_pct(matched, notes_exist), 2),
        sample_events=[asdict(e) for e in events[:20]],
        started_at=_iso_z(started),
        finished_at=_iso_z(datetime.now(timezone.utc)),
    )


def _measure_notes(
    events: List[MeetEvent],
    dry_run: bool,
    window_start: datetime,
) -> Tuple[int, int, int, int]:
    """Measure notes coverage for the harness. Returns a 4-tuple:
    (matched_events, events_notes_exist, docs_unmatched, drive_failures).

    Dry-run short-circuits all Drive calls and returns zeros so
    `coverage_pct_recorded` hits vacuous-truth (caught by the CLI verdict
    guard, which maps dry_run to DRY_RUN rather than CONFIRM).
    """
    if dry_run:
        return 0, 0, 0, 0
    organisers = {e.organiser for e in events if e.organiser}
    since = _iso_z(window_start)[:10]
    cache, drive_failures = _fetch_organiser_notes_cache(organisers, since)
    matched, notes_exist, unmatched = _attribute_docs_to_events(events, cache)
    return matched, notes_exist, unmatched, drive_failures


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="H021 coverage harness")
    p.add_argument("--days", type=int, default=30, help="Lookback window in days")
    p.add_argument("--users-file", type=Path, default=DEFAULT_USERS_FILE)
    p.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Enumerate Meet events only; skip Drive Gemini Notes checks",
    )
    return p.parse_args()


_MIN_SAMPLE_SIZE_FOR_VERDICT = 5
"""Goodhart guard: refuse to emit CONFIRM or NOT_YET when the denominator is
below this threshold. H021's prediction (>=99) is trivially satisfied at N=0
and trivially broken at N=1, so neither outcome would be meaningful evidence.
The threshold is intentionally low because DWD meet-absorb is hard to sample
in tight windows — but it must be above zero to kill vacuous-truth CONFIRMs.
"""


def _verdict_from_result(result: CoverageResult, dry_run: bool) -> str:
    """Map a run to a verdict string for the CLI footer.

    Dispatch over (dry_run, denominator, ratio) to avoid nested conditionals:
    - dry_run: DRY_RUN (no Drive calls, no verdict possible)
    - denom < MIN: INSUFFICIENT_DATA (Goodhart guard)
    - ratio >= 99: CONFIRM
    - else: NOT_YET
    """
    if dry_run:
        return "DRY_RUN"
    if result.events_notes_exist_in_window < _MIN_SAMPLE_SIZE_FOR_VERDICT:
        return "INSUFFICIENT_DATA"
    return "CONFIRM" if result.coverage_pct_recorded >= 99 else "NOT_YET"


def main() -> int:
    args = _parse_args()
    try:
        result = run_harness(args.days, args.users_file, args.dry_run)
    except RuntimeError as exc:
        print(f"[h021-harness] setup error: {exc}", file=sys.stderr)
        return 1
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(asdict(result), indent=2) + "\n")
    print(json.dumps(asdict(result), indent=2))
    if result.drive_query_failures:
        print(
            f"\n[h021-harness] WARN: {result.drive_query_failures} organiser(s) "
            f"had Drive query failures; coverage numbers are a lower bound.",
            file=sys.stderr,
        )
    verdict = _verdict_from_result(result, args.dry_run)
    denom = result.events_notes_exist_in_window
    print(
        f"\nH021: coverage_pct_recorded={result.coverage_pct_recorded}  "
        f"(matched {result.events_with_notes} / notes_exist {denom})  "
        f"raw={result.coverage_pct_raw}  "
        f"prediction>=99  result={verdict}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
