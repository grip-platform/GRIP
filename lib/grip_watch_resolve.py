#!/usr/bin/env python3
"""Turn a fuzzy query into a specific event with watch options.

This is the layer `lib/grip_watch.py` deliberately leaves out. That module
handles the viewer, link liveness, ranking and rendering; this one answers the
question before all of those: *which event, and where does IT say it can be
watched.*

Two invariants here are not stylistic. Both come from an end-to-end run on
2026-08-22 that returned a confidently wrong answer, and each is pinned by a
regression test:

**Future-only, verified per candidate.** Launch Library 2's ``upcoming/``
endpoint returned a launch that had already flown nine hours earlier. The name
of an endpoint is not a guarantee about its contents, so every candidate's start
time is compared against *now* here rather than trusted.

**Options come from the event, never from a table.** The same run answered a
Chinese lunar mission -- Chang'e 7, flown by CASC -- with a link to SpaceX's
feed. The URL resolved with HTTP 200 and looked entirely reasonable, which is
exactly what made it dangerous: link-checking cannot catch a wrong-provider
answer, because reachability was never the problem. Watch options are therefore
read from the event record's own ``vidURLs`` and from nothing else. When an
event publishes no stream, the honest output is an empty option list, and the
caller says so. A plausible guess is worse than an admission.

Only the launch class is implemented. That is deliberate: it is the class with a
free, keyless, structured source carrying a live status field, so it is where
this can be right nearly every time. Sport, award shows and concerts have no
comparable aggregator and are unavoidably search-shaped; guessing a source name
for them would reproduce the failure this module exists to prevent.
"""

from __future__ import annotations

import datetime
import json
import re
import sys
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Sequence

_UA = "GRIP-watch/1.0 (+https://github.com/grip-platform/GRIP)"

#: Launch Library 2. Free, no API key, 15 requests/hour unauthenticated.
_LL2 = "https://ll.thespacedevs.com/2.0.0/launch/upcoming/"

#: Words that place a query in the launch class.
_LAUNCH_WORDS = (
    "launch", "rocket", "spacex", "falcon", "starship", "nasa", "artemis",
    "ariane", "soyuz", "electron", "rocket lab", "blue origin", "new glenn",
    "vulcan", "long march", "starlink", "liftoff",
)

#: Words that carry no discriminating power in a "where can I watch" query.
#: Without these, "the SpaceX thing" requires a candidate containing "thing".
_STOPWORDS = {
    "the", "next", "launch", "watch", "thing", "stream", "live", "see", "where",
    "can", "when", "what", "for", "and", "with", "today", "tonight", "tomorrow",
    "one", "get", "got", "any", "how", "does", "did", "this", "that",
}

CLASS_LAUNCH = "launch"
CLASS_UNKNOWN = "unknown"


@dataclass(frozen=True)
class WatchOption:
    """A stream the EVENT itself publishes. Never constructed from a lookup table."""

    url: str
    title: str = ""
    source: str = "event record"


@dataclass(frozen=True)
class ResolvedEvent:
    """One specific event, with whatever it publishes about watching it."""

    name: str
    event_class: str
    starts_at: datetime.datetime | None = None
    starts_in_minutes: int | None = None
    status: str = "unknown"
    provider: str = "unknown"
    options: list[WatchOption] = field(default_factory=list)

    #: Status values meaning the event is expected to happen as scheduled.
    #: Anything else -- scrubbed, held, cancelled, TBD -- is not a normal
    #: watchable event and must not be rendered as one.
    _GO_STATUSES = frozenset({"go", "go for launch", "in flight", "tbc"})

    @property
    def has_stream(self) -> bool:
        """Whether the event published anywhere to watch it."""
        return bool(self.options)

    @property
    def is_go(self) -> bool:
        """Whether the source says this is still expected to happen.

        Enforced by ``resolve_launch(require_go=True)``, which is the default:
        a non-GO candidate is filtered out rather than returned with a flag.
        Pass ``require_go=False`` to inspect scrubbed events deliberately.
        """
        return self.status.strip().lower() in self._GO_STATUSES


def classify(query: str) -> str:
    """Place a free-text query in an event class, or return CLASS_UNKNOWN.

    Deliberately conservative. An unknown class is a useful answer -- it tells
    the caller to fall through to a live search rather than pretending a
    structured source applies.
    """
    lowered = (query or "").lower()
    if any(word in lowered for word in _LAUNCH_WORDS):
        return CLASS_LAUNCH
    return CLASS_UNKNOWN


def _fetch_json(url: str, timeout: float) -> dict[str, Any] | None:
    """GET and parse JSON. Returns None on any failure; never raises."""
    try:
        request = urllib.request.Request(url, headers={"User-Agent": _UA})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8", errors="replace"))
    except Exception:
        return None


def parse_net(value: str | None) -> datetime.datetime | None:
    """Parse Launch Library's NET timestamp. Returns None rather than raising."""
    if not value:
        return None
    try:
        parsed = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.timezone.utc)
    return parsed


def options_from_record(record: dict[str, Any]) -> list[WatchOption]:
    """Read watch options from the event's OWN published stream list.

    Nothing else is consulted. If the record publishes no ``vidURLs``, the
    result is empty and the caller must say so rather than substitute a
    provider's usual channel -- that substitution is what produced a SpaceX
    link for a CASC launch.
    """
    out: list[WatchOption] = []
    for entry in record.get("vidURLs") or []:
        if not isinstance(entry, dict):
            continue
        url = (entry.get("url") or "").strip()
        if url:
            out.append(WatchOption(url=url, title=(entry.get("title") or "").strip()))
    return out


def future_candidates(
    records: Sequence[dict[str, Any]], now: datetime.datetime
) -> list[tuple[datetime.datetime, dict[str, Any]]]:
    """Records that start strictly after ``now``, soonest first.

    The endpoint is called ``upcoming`` and has been observed returning a launch
    that flew nine hours earlier, so futureness is established here rather than
    assumed from the URL.
    """
    dated: list[tuple[datetime.datetime, dict[str, Any]]] = []
    for record in records:
        starts = parse_net(record.get("net"))
        if starts is not None and starts > now:
            dated.append((starts, record))
    dated.sort(key=lambda pair: pair[0])
    return dated


def _status_of(record: dict[str, Any]) -> str:
    status = record.get("status")
    if isinstance(status, dict):
        return status.get("abbrev") or status.get("name") or "unknown"
    return "unknown"


def _provider_of(record: dict[str, Any]) -> str:
    provider = record.get("launch_service_provider")
    if isinstance(provider, dict):
        return provider.get("name") or "unknown"
    return "unknown"


def event_from_record(
    record: dict[str, Any], now: datetime.datetime
) -> ResolvedEvent:
    """Build a ResolvedEvent from one Launch Library record."""
    starts = parse_net(record.get("net"))
    minutes = None
    if starts is not None:
        minutes = int((starts - now).total_seconds() // 60)
    return ResolvedEvent(
        name=record.get("name") or "unnamed launch",
        event_class=CLASS_LAUNCH,
        starts_at=starts,
        starts_in_minutes=minutes,
        status=_status_of(record),
        provider=_provider_of(record),
        options=options_from_record(record),
    )


def resolve_launch(
    query: str,
    now: datetime.datetime | None = None,
    limit: int = 10,
    timeout: float = 20.0,
    fetch: Callable[[str, float], dict[str, Any] | None] | None = None,
    require_go: bool = True,
) -> ResolvedEvent | None:
    """Resolve a launch query to the soonest matching FUTURE launch.

    ``fetch`` is injectable so tests exercise the selection logic hermetically
    without a network call. Returns None when the source is unreachable or has
    no future launch -- both are honest answers, and neither is an error.
    """
    moment = now or datetime.datetime.now(datetime.timezone.utc)
    getter = fetch or _fetch_json
    url = (
        f"{_LL2}?limit={limit}&mode=detailed"
        f"&net__gte={moment.strftime('%Y-%m-%dT%H:%M:%SZ')}"
    )
    payload = getter(url, timeout)
    if not payload:
        return None

    candidates = future_candidates(payload.get("results") or [], moment)
    if require_go:
        # ENFORCED, not merely recorded. An earlier version exposed `is_go` and
        # then returned the event anyway, so a scrubbed launch was still printed
        # as the answer with its likely-dead stream and H713 logged it as a
        # successful resolution. A property nothing consults is decoration.
        candidates = [
            (when, record) for when, record in candidates
            if _status_of(record).strip().lower() in ResolvedEvent._GO_STATUSES
        ]
    if not candidates:
        return None

    terms = [
        term for term in re.split(r"[^a-z0-9]+", (query or "").lower())
        if len(term) > 2 and term not in _STOPWORDS
    ]
    if terms:
        # EVERY term must appear, not any of them. `any` returned a
        # Falcon 9 / Starlink mission for "SpaceX Starship" -- the provider
        # matched, the vehicle did not, and the wrong event was rendered as the
        # answer. That is the same wrong-event failure the second invariant
        # above exists to prevent, arriving through the matcher instead of
        # through a provider table.
        for _, record in candidates:
            haystack = (
                f"{record.get('name', '')} {_provider_of(record)}"
            ).lower()
            if all(term in haystack for term in terms):
                return event_from_record(record, moment)
        # Nothing matched what was actually asked for. Returning the soonest
        # launch here would be a silent guess -- and this module's own
        # docstring forbids exactly that. It was reachable: "NASA documentary"
        # classifies as a launch on the substring "nasa", matches no candidate,
        # and used to come back with whatever Falcon was next, URL-checked and
        # rendered as though it were the answer. None is the honest result; the
        # caller already prints that nothing was found.
        return None
    return event_from_record(candidates[0][1], moment)


def resolve(query: str, now: datetime.datetime | None = None) -> ResolvedEvent | None:
    """Resolve any supported query. Returns None when the class is unsupported."""
    if classify(query) == CLASS_LAUNCH:
        return resolve_launch(query, now=now)
    return None


def access_label(event: ResolvedEvent) -> str:
    """What is honestly known about watching this, as a label.

    Deliberately NOT "free". An earlier version assigned and recorded that in
    the same expression, so the file could never emit "paywalled" and H713's
    access limb could never fire -- unfalsifiable by construction. The
    provenance of a link is knowable here; the viewer's entitlement to it is
    not, and deciding that needs a probe from the viewer's own network which
    nothing in this module performs.
    """
    return "first_party_unverified" if event.options else "none_published"


def _log(record: dict[str, Any]) -> None:
    """Append one H713 trial. A broken instrument reports; it never pretends."""
    try:
        from grip_watch import log_trial

        record.setdefault(
            "ts", datetime.datetime.now(datetime.timezone.utc).isoformat())
        if log_trial(record) is None:
            print("[grip-watch] note: trial not logged (H713 incomplete)",
                  file=sys.stderr)
    except Exception as exc:
        print(f"[grip-watch] note: trial logging failed ({type(exc).__name__}); "
              "H713 incomplete", file=sys.stderr)


def main(argv: Sequence[str] | None = None) -> int:
    """CLI: ``python3 lib/grip_watch_resolve.py "<query>"``."""
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        print('usage: grip_watch_resolve.py "<query>"', file=sys.stderr)
        return 2
    query = " ".join(a for a in args if not a.startswith("--"))
    event = resolve(query)
    klass = classify(query)

    if event is None:
        # Logged BEFORE returning. An earlier version returned here first, so
        # every unresolved query -- exactly the outcomes H713 needs in order to
        # be falsifiable -- was missing from the record entirely.
        _log({"query": query, "class": klass, "resolved": False,
              "reason": "unsupported_class" if klass == CLASS_UNKNOWN
                        else "no_future_event_or_source_unreachable"})
        if klass == CLASS_UNKNOWN:
            print(f"UNSUPPORTED CLASS: no structured source for {query!r}; needs a live search")
        else:
            print(f"NO FUTURE EVENT for {query!r} (source unreachable, or nothing scheduled)")
        return 1
    # H713 is falsified on two things: whether the link was reachable, and
    # whether a paywalled or geo-locked result was labelled as such. Neither was
    # recorded, so the hypothesis could not have been decided from this file.
    reachable = None
    if event.options:
        try:
            from grip_watch import check_link

            reachable = any(check_link(o.url, timeout=5.0).reachable
                            for o in event.options)
        except Exception:
            reachable = None

    _log({
        "query": query, "class": event.event_class, "resolved": True,
        "event": event.name, "provider": event.provider,
        "options": len(event.options), "has_stream": event.has_stream,
        "starts_in_minutes": event.starts_in_minutes,
        "status": event.status, "is_go": event.is_go,
        "any_option_reachable": reachable,
        # NOT "free". The label was assigned and recorded by the same
        # expression, so this file could never emit "paywalled" and the H713
        # limb "any result labelled free proves paywalled" could never fire --
        # unfalsifiable by construction, which is the exact defect the
        # hypothesis section claims to have corrected. What is actually known
        # is the provenance of the link, not the viewer's entitlement to it.
        "access": access_label(event),
    })

    # The documented three-line answer, produced by the same `render` the skill
    # promises. Until now no executable path emitted it: the CLI printed raw
    # JSON with neither an access label nor a region, so the shape the command
    # file advertises existed only in tests.
    from grip_watch import Option, render, resolve_viewer

    viewer = resolve_viewer()
    options = [
        Option(
            service=o.title or f"{event.provider} official",
            url=o.url,
            # "unknown", not "free": the provenance of the link is known, the
            # viewer's entitlement to it is not, and stating otherwise is the
            # unfalsifiable claim this converged away from.
            access="unknown",
            region="",
            note="from the event's own record",
        )
        for o in event.options
    ]
    print(render(f"{event.name} [{event.status}]", event.starts_in_minutes,
                 options, viewer))
    if not options:
        print(f"No stream published by {event.provider}.")
    if "--json" in args:
        print(json.dumps({
            "name": event.name, "class": event.event_class,
            "starts_in_minutes": event.starts_in_minutes,
            "status": event.status, "is_go": event.is_go,
            "provider": event.provider, "region": viewer.region,
            "access": access_label(event),
            "options": [{"url": o.url, "title": o.title} for o in event.options],
            "has_stream": event.has_stream,
        }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
