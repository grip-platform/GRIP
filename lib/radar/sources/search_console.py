"""Search queries that led to codetonight.co.za and happi.md, from Google Search Console.

``searchanalytics.query`` on each property for the newest day the API has
published (it lags about two days), with the day's totals and the top three
queries. Read with the service-account key file ``lib/meet_absorb_dwd.py``
uses, scope ``webmasters.readonly`` and NO impersonation: each property is
shared with the service-account address instead. Roll-up only: a property with
clicks or impressions gives one clause, once per published day.

Nothing here alerts. No properties, no key file, no client library, a property
not shared (403): each is a note shown by ``--status`` or a per-property skip in
the log. The schedule runs this source once a day, after 08:00 SAST
(``schedule.due``).
"""
from __future__ import annotations

import importlib.util
import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from ..model import Result

NAME = "search_console"
SCOPE = "https://www.googleapis.com/auth/webmasters.readonly"
NO_PROPERTIES = "no properties in config/radar.local.json (search_console.properties)"
NO_CLIENT = "google-api-python-client / google-auth not installed in the venv"
LAG_DAYS = 2


@dataclass
class SearchConsoleDeps:
    ready: Callable[[], str | None]                         # None when credentials resolve, else why not
    query: Callable[[str, str, str, list[str], int], dict]  # (property, start, end, dimensions, row_limit)
    sa_address: Callable[[], str]                           # the service-account e-mail, for the hint
    errors: tuple[type[BaseException], ...] = (Exception,)


class FixtureError(Exception):
    """A forced API failure in fixture mode."""


def label(prop: str) -> str:
    """``sc-domain:codetonight.co.za`` -> ``codetonight.co.za``; ``https://happi.md/`` -> ``happi.md``."""
    text = prop.strip()
    for prefix in ("sc-domain:", "https://", "http://"):
        if text.startswith(prefix):
            text = text[len(prefix):]
    return text.rstrip("/")


def _rows(resp) -> list[dict]:
    return [r for r in ((resp or {}).get("rows") or []) if isinstance(r, dict)]


def _read(deps: SearchConsoleDeps, prop: str, day: str) -> dict:
    totals = _rows(deps.query(prop, day, day, [], 1))
    first = totals[0] if totals else {}
    top = _rows(deps.query(prop, day, day, ["query"], 3)) if totals else []
    return {"day": day, "clicks": int(first.get("clicks") or 0), "impressions": int(first.get("impressions") or 0),
            "top_queries": [{"query": str((r.get("keys") or [""])[0]), "clicks": int(r.get("clicks") or 0),
                             "impressions": int(r.get("impressions") or 0)} for r in top if r.get("keys")]}


def _read_latest(deps: SearchConsoleDeps, prop: str, latest) -> dict:
    """The newest published day: ``latest`` or, when it has no rows yet, the day before."""
    cur = _read(deps, prop, latest.isoformat())
    if cur["clicks"] or cur["impressions"]:
        return cur
    earlier = _read(deps, prop, (latest - timedelta(days=1)).isoformat())
    return earlier if (earlier["clicks"] or earlier["impressions"]) else cur


def _reason(exc: BaseException, deps: SearchConsoleDeps, prop: str) -> str:
    text = str(exc)
    status = getattr(getattr(exc, "resp", None), "status", None)
    if status == 403 or "403" in text[:60]:
        return f"property not shared with {deps.sa_address()}"
    if status == 404 or "404" in text[:60]:
        return f"property not found: {prop}"
    return f"{type(exc).__name__}: {text[:80]}"


def _noted(sl: dict, note: str) -> Result:
    sl["note"] = note
    sl.setdefault("properties", {})
    return Result(sl, [], None, {"note": note})


def collect(slice_: dict, deps: SearchConsoleDeps, *, now: datetime, config: dict) -> Result:
    cfg = config.get("search_console", {})
    sl = dict(slice_)
    props = [p for p in cfg.get("properties", []) if isinstance(p, str) and p.strip()]
    if not props:
        return _noted(sl, NO_PROPERTIES)
    reason = deps.ready()
    if reason:
        return _noted(sl, reason)
    sl.pop("note", None)
    latest = now.astimezone(timezone.utc).date() - timedelta(days=LAG_DAYS)
    states = dict(sl.get("properties", {}))
    detail: dict = {}
    for prop in props:
        prev = states.get(prop, {})
        try:
            cur = _read_latest(deps, prop, latest)
        except deps.errors as exc:
            cur = {**prev, "skipped": _reason(exc, deps, prop)}
            detail[prop] = cur["skipped"]
        else:
            cur["reported_day"] = prev.get("reported_day")
        states[prop] = cur
    sl["properties"] = states
    return Result(sl, [], None, detail)


def describe(result: Result) -> str:
    sl = result.slice
    if sl.get("note"):
        return sl["note"]
    parts = [f"{label(prop)} skipped: {p['skipped']}" if p.get("skipped")
             else f"{label(prop)} {p.get('clicks', 0)} clicks/{p.get('impressions', 0)} impr ({p.get('day')})"
             for prop, p in sl.get("properties", {}).items()]
    return "; ".join(parts) or "no properties"


def rollup_clauses(sl: dict) -> list[str]:
    """One clause per property with movement on a day not yet reported; marks it reported."""
    out = []
    for prop, p in sl.get("properties", {}).items():
        if p.get("skipped") or not (p.get("clicks") or p.get("impressions")) or p.get("reported_day") == p.get("day"):
            continue
        top = ((p.get("top_queries") or [{}])[0] or {}).get("query")
        clause = f"search {label(prop)} {p['clicks']} clicks/{p['impressions']} impr"
        if top:
            clause += f' "{top}"'
        out.append(clause + f" ({p['day']})")
        p["reported_day"] = p["day"]
    return out


# ---------------------------------------------------------------------------
# Real and fixture I/O
# ---------------------------------------------------------------------------
def _key_path() -> Path | None:
    import meet_absorb_dwd as dwd
    env = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "").strip()
    for candidate in ([Path(env)] if env else []) + [dwd.DEFAULT_KEY_PATH]:
        if candidate.is_file():
            return candidate
    return None


def default_deps(config: dict) -> SearchConsoleDeps:
    def ready() -> str | None:
        if importlib.util.find_spec("googleapiclient") is None or importlib.util.find_spec("google.oauth2") is None:
            return NO_CLIENT
        if _key_path() is None:
            import meet_absorb_dwd as dwd
            return f"no service-account key ({dwd.DEFAULT_KEY_PATH} or GOOGLE_APPLICATION_CREDENTIALS)"
        return None

    def sa_address() -> str:
        try:
            return str(json.loads(_key_path().read_text(encoding="utf-8")).get("client_email") or "the service account")
        except (OSError, ValueError, AttributeError):
            return "the service account"

    def query(prop: str, start: str, end: str, dimensions: list[str], row_limit: int) -> dict:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build
        creds = service_account.Credentials.from_service_account_file(str(_key_path()), scopes=[SCOPE])
        service = build("searchconsole", "v1", credentials=creds, cache_discovery=False)
        body = {"startDate": start, "endDate": end, "rowLimit": row_limit}
        if dimensions:
            body["dimensions"] = dimensions
        return service.searchanalytics().query(siteUrl=prop, body=body).execute()

    return SearchConsoleDeps(ready=ready, query=query, sa_address=sa_address, errors=(Exception,))


def fixture_deps(directory: Path) -> SearchConsoleDeps:
    """``sc_totals.json`` and ``sc_query.json`` answer every property; a property
    listed in ``sc_status.json`` raises with that status instead."""
    directory = Path(directory)

    def _json(name: str):
        path = directory / name
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None

    def query(prop: str, start: str, end: str, dimensions: list[str], row_limit: int) -> dict:
        statuses = _json("sc_status.json") or {}
        if prop in statuses:
            raise FixtureError(f"HttpError {statuses[prop]} for {prop}")
        return _json("sc_query.json" if dimensions else "sc_totals.json") or {}

    return SearchConsoleDeps(ready=lambda: None, query=query,
                             sa_address=lambda: "radar-fixture@example.iam.gserviceaccount.com",
                             errors=(FixtureError,))
