"""Visits to grasp-web and happi.md through Vercel's Web Analytics API.

``GET https://api.vercel.com/v1/query/web-analytics/visits/{count|aggregate}``
with ``Authorization: Bearer <token>``, ``projectId``, ``teamId`` when the
project is team-owned, ``since`` and ``until`` as millisecond epochs, and for
the aggregate ``by=referrerHostname&limit=3``. Without a token the API answers
403 ``{"error": {"code": "forbidden", "missingToken": true}}`` (probed
2026-10-21).

The token comes from ``VERCEL_TOKEN`` or the keychain service ``grip-vercel``
(``lib/provider_secret.py``); the projects come from the gitignored
``config/radar.local.json``. Either missing is a note shown by ``--status``,
never an alert and never a crash, and the source keeps its hourly interval so a
token added later is picked up within the hour.

Each tick reads the current SAST day's count. On the first tick after midnight
it reads the finished day once more, with its referrers, and keeps that as
``completed`` for the 08:00 roll-up. The field names the API answers with are
pinned on the first authenticated run (``--once --dry-run --source
vercel_site``); an unexpected shape is a per-project skip in the log.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable
from urllib.parse import urlencode

from .. import fetch
from ..fetch import Fetched
from ..fixtures import fixture_http
from ..model import Result
from ..schedule import SAST, sast_day

NAME = "vercel_site"
API = "https://api.vercel.com/v1/query/web-analytics/visits"
NO_TOKEN = "no token (VERCEL_TOKEN / keychain grip-vercel)"
NO_PROJECTS = "no projects in config/radar.local.json (vercel.projects)"
COUNT_KEYS = ("count", "total", "visits", "visitors", "value")
NEST_KEYS = ("data", "result", "results", "rows")
HOST_KEYS = ("referrerHostname", "key", "name", "label", "value")


@dataclass
class VercelDeps:
    http: Callable[..., Fetched]
    token: Callable[[], str | None]


class _Network(Exception):
    """The API could not be reached: the whole source waits for the next tick."""


def _ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def _day_start(day: str) -> datetime:
    return datetime.fromisoformat(day).replace(tzinfo=SAST)


def visit_count(data) -> int | None:
    """The number in whatever shape the count endpoint answers, else None."""
    if isinstance(data, bool):
        return None
    if isinstance(data, (int, float)):
        return int(data)
    if isinstance(data, list):
        return visit_count(data[0]) if data else None
    if not isinstance(data, dict):
        return None
    for key in COUNT_KEYS:
        value = data.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return int(value)
    for key in NEST_KEYS:
        if key in data:
            found = visit_count(data[key])
            if found is not None:
                return found
    return None


def referrers(data) -> list[dict]:
    """Top referrer hosts out of the aggregate answer, most visits first."""
    rows = data
    if isinstance(data, dict):
        rows = next((data[k] for k in NEST_KEYS if isinstance(data.get(k), list)), [])
    out = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        host = next((row[k] for k in HOST_KEYS if isinstance(row.get(k), str)), None)
        count = visit_count({k: v for k, v in row.items() if k in COUNT_KEYS}) or 0
        if host:
            out.append({"referrer": host, "count": count})
    return out[:3]


def _url(kind: str, project: dict, since: datetime, until: datetime, **extra) -> str:
    params = {"projectId": project["projectId"], "since": _ms(since), "until": _ms(until)}
    if project.get("teamId"):
        params["teamId"] = project["teamId"]
    params.update(extra)
    return f"{API}/{kind}?{urlencode(params)}"


def _reason(res: Fetched, name: str) -> str:
    if res.status in (401, 403):
        return f"analytics not enabled or token not valid for {name} ({res.skip})"
    return str(res.skip)


def _count(deps: VercelDeps, headers: dict, project: dict, since: datetime, until: datetime,
           name: str) -> tuple[int | None, str | None]:
    res = deps.http(_url("count", project, since, until), headers=headers)
    if not res.ok:
        if str(res.skip).startswith(("network", "timeout")):
            raise _Network(str(res.skip))
        return None, _reason(res, name)
    visits = visit_count(res.json())
    return (visits, None) if visits is not None else (None, "unexpected response shape")


def _top_referrers(deps: VercelDeps, headers: dict, project: dict, since: datetime, until: datetime) -> list[dict]:
    res = deps.http(_url("aggregate", project, since, until, by="referrerHostname", limit=3), headers=headers)
    return referrers(res.json()) if res.ok else []


def _project(deps: VercelDeps, headers: dict, project: dict, prev: dict, today: str, detail: dict) -> dict:
    name = project.get("name") or project["projectId"]
    cur = {"day": today, "visits": prev.get("visits") if prev.get("day") == today else None,
           "completed": prev.get("completed")}
    if prev.get("day") and prev["day"] != today:  # a day finished: read its final figures once
        start = _day_start(prev["day"])
        visits, skip = _count(deps, headers, project, start, start + timedelta(days=1), name)
        if skip is None:
            cur["completed"] = {"day": prev["day"], "visits": visits,
                                "referrers": _top_referrers(deps, headers, project, start, start + timedelta(days=1))}
        else:
            detail[f"{name} completed day"] = skip
    start = _day_start(today)
    visits, skip = _count(deps, headers, project, start, start + timedelta(days=1), name)
    if skip:
        cur["skipped"] = skip
        detail[name] = skip
    else:
        cur["visits"] = visits
    return cur


def _noted(sl: dict, note: str) -> Result:
    sl["note"] = note
    sl.setdefault("projects", {})
    return Result(sl, [], None, {"note": note})


def collect(slice_: dict, deps: VercelDeps, *, now: datetime, config: dict) -> Result:
    cfg = config.get("vercel", {})
    sl = dict(slice_)
    projects = [p for p in cfg.get("projects", []) if isinstance(p, dict) and p.get("projectId")]
    if not projects:
        return _noted(sl, NO_PROJECTS)
    token = deps.token()
    if not token:
        return _noted(sl, NO_TOKEN)
    sl.pop("note", None)
    headers = {"Authorization": f"Bearer {token}"}
    today = sast_day(now)
    states = dict(sl.get("projects", {}))
    detail: dict = {}
    try:
        for project in projects:
            name = project.get("name") or project["projectId"]
            states[name] = _project(deps, headers, project, states.get(name, {}), today, detail)
    except _Network as exc:
        return Result(dict(slice_), [], skipped=f"network: vercel ({exc})", detail=detail)
    sl["projects"] = states
    return Result(sl, [], None, detail)


def describe(result: Result) -> str:
    if result.skipped:
        return f"skipped: {result.skipped}"
    sl = result.slice
    if sl.get("note"):
        return sl["note"]
    parts = [f"{name} skipped: {p['skipped']}" if p.get("skipped") else f"{name} {p.get('visits', '?')} visits today"
             for name, p in sl.get("projects", {}).items()]
    return "; ".join(parts) or "no projects"


def rollup_clauses(sl: dict, day: str) -> list[str]:
    """One clause per project whose finished ``day`` had visits."""
    out = []
    for name, p in sl.get("projects", {}).items():
        done = p.get("completed") or {}
        if done.get("day") != day or not done.get("visits"):
            continue
        top = ((done.get("referrers") or [{}])[0] or {}).get("referrer")
        noun = "visit" if done["visits"] == 1 else "visits"
        out.append(f"{name} {done['visits']} {noun}" + (f" top {top}" if top else ""))
    return out


# ---------------------------------------------------------------------------
# Real and fixture I/O
# ---------------------------------------------------------------------------
def default_deps(config: dict) -> VercelDeps:
    def token() -> str | None:
        env = os.environ.get("VERCEL_TOKEN", "").strip()
        if env:
            return env
        from provider_secret import resolve_provider_key
        return resolve_provider_key("grip-vercel")

    return VercelDeps(http=fetch.http, token=token)


def fixture_deps(directory: Path) -> VercelDeps:
    return VercelDeps(http=fixture_http(directory), token=lambda: "fixture-token")
