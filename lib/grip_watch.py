#!/usr/bin/env python3
"""Deterministic primitives for GRIP's ``/grip-watch`` skill.

This module deliberately does not discover events or decide which services
carry them.  It resolves the viewer context available on the local machine,
checks whether a supplied URL resolves, orders already-discovered options, and
renders the best of those options for a terminal.

The boundary is intentionally small and standard-library-only so callers can
use it without loading the rest of GRIP and tests can replace the network seam.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal


_TZ_REGION: dict[str, str] = {
    "Africa/Johannesburg": "ZA",
    "Europe/London": "GB",
    "Europe/Dublin": "IE",
    "America/New_York": "US",
    "America/Los_Angeles": "US",
    "Australia/Sydney": "AU",
    "Pacific/Auckland": "NZ",
}


@dataclass(frozen=True)
class Viewer:
    """The viewer's ISO-3166 alpha-2 region and IANA timezone."""

    region: str
    timezone: str


def _local_timezone() -> str:
    """Read an IANA timezone from the ``/etc/localtime`` symlink, if present."""
    try:
        target = os.readlink("/etc/localtime")
        marker = "/zoneinfo/"
        if marker not in target:
            return ""
        return target.split(marker, 1)[1].strip("/")
    except (OSError, TypeError, ValueError):
        return ""


def resolve_viewer(env: dict[str, str] | None = None) -> Viewer:
    """Resolve viewer context without guessing, and never raise.

    The first rung with information wins:

    1. ``GRIP_WATCH_REGION`` or ``GRIP_WATCH_TZ`` explicitly supplies the
       corresponding values. This rung wins if either variable is non-empty.
    2. ``/etc/localtime`` is inspected when it is a symlink into ``zoneinfo``;
       its IANA timezone is mapped through the small explicit
       :data:`_TZ_REGION` table. An unmapped timezone is not a usable hit.
    3. No evidence returns ``Viewer(region="", timezone="")``. This is the
       visible UNKNOWN result, never a guessed default.
    """
    try:
        source = os.environ if env is None else env
        region = str(source.get("GRIP_WATCH_REGION", "") or "").strip().upper()
        timezone = str(source.get("GRIP_WATCH_TZ", "") or "").strip()
        if region or timezone:
            return Viewer(region=region, timezone=timezone)

        timezone = _local_timezone()
        region = _TZ_REGION.get(timezone, "")
        if region:
            return Viewer(region=region, timezone=timezone)
    except Exception:
        # Environment mappings can be caller supplied and need not be ordinary
        # dicts. Viewer resolution is a best-effort boundary and must not leak
        # their failures.
        pass
    return Viewer(region="", timezone="")


@dataclass(frozen=True)
class LinkCheck:
    """The result of checking whether a URL resolves over HTTP."""

    url: str
    status: int | None
    reachable: bool
    note: str


def check_link(url: str, timeout: float = 4.0) -> LinkCheck:
    """Check whether ``url`` resolves, using HEAD then GET for 405 or 501.

    Redirects are followed by urllib's default opener. No network failure is
    raised to the caller. Critically, HTTP 200 proves only that the URL
    *resolves*. It does not prove the viewer can watch: geo-blocks and paywalls
    commonly return 200 with a "not available in your region" page.
    """
    def request(method: str):
        return urllib.request.urlopen(  # noqa: S310 - caller intentionally supplies the URL
            urllib.request.Request(url, method=method), timeout=timeout
        )

    try:
        try:
            response = request("HEAD")
        except urllib.error.HTTPError as exc:
            if exc.code not in (405, 501):
                return LinkCheck(url, exc.code, False, f"HTTP {exc.code}: {exc.reason}")
            response = request("GET")

        with response:
            status = getattr(response, "status", None)
            if status is None:
                status = response.getcode()
        reachable = status is not None and 200 <= status < 400
        note = "URL resolves; viewing access is not verified" if reachable else f"HTTP {status}"
        return LinkCheck(url, status, reachable, note)
    except urllib.error.HTTPError as exc:
        return LinkCheck(url, exc.code, False, f"HTTP {exc.code}: {exc.reason}")
    except Exception as exc:
        reason = str(exc).strip() or exc.__class__.__name__
        return LinkCheck(url, None, False, reason)


Access = Literal["free", "subscription", "geo-blocked", "unknown"]


@dataclass(frozen=True)
class Option:
    """One already-discovered way the event may be watched."""

    service: str
    url: str
    access: Access
    region: str
    note: str


_ACCESS_ORDER: dict[str, int] = {
    "free": 0,
    "subscription": 1,
    "geo-blocked": 2,
    "unknown": 3,
}


def rank(options: list[Option], viewer: Viewer) -> list[Option]:
    """Return a stable deterministic ordering by region match, then access."""
    def key(option: Option) -> tuple[int, int]:
        in_region = bool(viewer.region) and option.region == viewer.region
        return (0 if in_region else 1, _ACCESS_ORDER.get(option.access, 3))

    return sorted(options, key=key)


def render(
    event_name: str,
    starts_in_minutes: int | None,
    options: list[Option],
    viewer: Viewer,
) -> str:
    """Render a terse terminal answer with the single best option first."""
    ordered = rank(options, viewer)
    if starts_in_minutes is None:
        timing = "Time unknown"
    elif starts_in_minutes < 0:
        timing = f"Started {abs(starts_in_minutes)} minute(s) ago"
    elif starts_in_minutes == 0:
        timing = "Starting now"
    else:
        timing = f"Starts in {starts_in_minutes} minute(s)"

    region = viewer.region or "unknown"
    if not ordered:
        return f"NO WATCH OPTION\n{event_name} — {timing}\nRegion: {region}"

    best = ordered[0]
    detail = f"{best.service} ({best.access})"
    if best.note:
        detail += f" — {best.note}"
    return f"WATCH: {best.url}\n{detail}\n{event_name} — {timing}\nRegion: {region}"


def log_trial(record: dict, root: Path | None = None) -> Path | None:
    """Append one trial to ``state/grip-watch-trials.jsonl``.

    Hypothesis H713 is measured from this file, and until it existed the
    hypothesis was a promise with no instrument -- the skill documented a
    measurement nothing performed. The path is resolved from THIS file rather
    than from CLAUDE_DIR: the trials belong to the checkout being exercised, and
    resolving the shared install root here would write one checkout's evidence
    into another's.

    Never raises. A lost trial line costs a data point; an exception here would
    cost the answer the operator actually asked for.
    """
    base = root or Path(__file__).resolve().parent.parent
    path = base / "state" / "grip-watch-trials.jsonl"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, sort_keys=True) + "\n")
        return path
    except OSError:
        return None


def main(argv: list[str]) -> int:
    """CLI: check one URL or print the resolved viewer as JSON."""
    parser = argparse.ArgumentParser(prog="grip-watch")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--check", metavar="URL")
    group.add_argument("--viewer", action="store_true")
    args = parser.parse_args(argv[1:])

    if args.viewer:
        print(json.dumps(asdict(resolve_viewer()), sort_keys=True))
        return 0
    result = check_link(args.check)
    print(json.dumps(asdict(result), sort_keys=True))
    return 0 if result.reachable else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
