"""IETF movement: a new revision of the TMIF draft, and RATS list mail that
mentions the work.

The datatracker's JSON (``/api/v1/doc/document/?name=draft-laurie-tmif``)
carries ``objects[0].rev`` as a string such as ``"02"`` (probed 2026-10-21: rev
02, time 2026-09-16T16:22:54Z). A higher revision than the one recorded is a
``rev`` change, pushed at once; the Atom feed of document changes names the
submitter for the line. The RATS archive refuses scripted search, but its
browse index (``/arch/browse/rats/``) answers with one ``div.xtr`` row per
message: the subject in ``a.msg-detail`` with ``href="/arch/msg/rats/<id>/"``,
then ``div.from-col`` and ``div.date-col``. New ids are matched on the subject,
and when the subject says nothing, the message page is fetched (at most 20 per
tick, one second apart) and its ``pre.wordwrap`` body matched too. An id is
marked seen only once it has been judged, so a page that could not be fetched is
retried next tick.
"""
from __future__ import annotations

import html as html_lib
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

from .. import fetch
from ..fetch import Fetched, Pacer
from ..fixtures import fixture_http
from ..model import Result, change
from ..state import cap

NAME = "ietf"
DATATRACKER = "https://datatracker.ietf.org"
ARCHIVE = "https://mailarchive.ietf.org"
DEFAULT_TERMS = ("GRASP", "cryptographic causation", "codetonight", "TMIF")  # a person's name: ietf.extra_terms in the local file
MAX_PAGES = 20

_ROW_RE = re.compile(
    r'href="/arch/msg/(?P<list>[^/"]+)/(?P<id>[^/"]+)/?"[^>]*>(?P<subject>.*?)</a>\s*</div>\s*'
    r'<div class="xtd from-col">(?P<from>.*?)</div>\s*<div class="xtd date-col">(?P<date>[^<]*)</div>',
    re.S)
_PRE_RE = re.compile(r'<pre class="wordwrap">(.*?)</pre>', re.S)
_TAG_RE = re.compile(r"<[^>]+>")
_ATOM_TITLE_RE = re.compile(r"<entry>.*?<title>\[(.*?)\]", re.S)


@dataclass
class IetfDeps:
    http: Callable[..., Fetched]
    pacer: Pacer


def _text(fragment: str) -> str:
    return html_lib.unescape(_TAG_RE.sub(" ", fragment)).strip()


def parse_browse(page: str) -> list[dict]:
    """``[{"id", "subject", "from", "date"}]`` for each row of a browse index, newest first."""
    rows = []
    for m in _ROW_RE.finditer(page):
        rows.append({"id": m.group("id"), "subject": _text(m.group("subject")),
                     "from": _text(m.group("from")), "date": m.group("date").strip()})
    return rows


def message_text(page: str) -> str:
    m = _PRE_RE.search(page)
    return _text(m.group(1) if m else page)


def matched_term(text: str, terms) -> str | None:
    low = text.lower()
    return next((t for t in terms if t.lower() in low), None)


def _submitter(deps: IetfDeps, draft: str) -> str:
    res = deps.http(f"{DATATRACKER}/feed/document-changes/{draft}/")
    m = _ATOM_TITLE_RE.search(res.body) if res.ok else None
    return _text(m.group(1)) if m else ""


def _draft(sl: dict, deps: IetfDeps, cfg: dict, detail: dict) -> tuple[list[dict], str | None]:
    draft = cfg["draft"]
    res = deps.http(f"{DATATRACKER}/api/v1/doc/document/?name={draft}&format=json")
    if not res.ok:
        return [], str(res.skip)
    objects = (res.json() or {}).get("objects") or []
    if not objects:
        detail["draft"] = "no document in the datatracker answer"
        return [], None
    rev = int(str(objects[0].get("rev") or "0"))
    prev = sl.get("draft_rev")
    changes = []
    if prev is not None and rev > int(prev):
        changes.append(change("rev", NAME, draft=draft, old_rev=int(prev), new_rev=rev,
                              date=str(objects[0].get("time") or "")[:10], by=_submitter(deps, draft),
                              url=f"{DATATRACKER}/doc/{draft}/{rev:02d}/"))
    sl["draft_rev"], sl["draft_time"] = rev, str(objects[0].get("time") or "")
    return changes, None


def _judge(row: dict, deps: IetfDeps, terms, budget: list[int], detail: dict) -> str | None | bool:
    """The matched term, None for no match, or False when the page could not be read."""
    term = matched_term(row["subject"], terms)
    if term is not None:
        return term
    if budget[0] <= 0:
        return False  # the rest of this tick's new ids wait for the next tick
    budget[0] -= 1
    deps.pacer.wait()
    page = deps.http(f"{ARCHIVE}/arch/msg/rats/{row['id']}/")
    if not page.ok:
        detail[f"rats {row['id']}"] = page.skip
        return False
    return matched_term(message_text(page.body), terms)


def _rats(sl: dict, deps: IetfDeps, cfg: dict, detail: dict) -> list[dict]:
    res = deps.http(cfg.get("rats_browse_url", f"{ARCHIVE}/arch/browse/rats/"))
    if not res.ok:
        detail["rats"] = res.skip
        return []
    rows = parse_browse(res.body)
    seeding = "rats_seen_ids" not in sl
    seen = list(sl.get("rats_seen_ids", []))
    terms = tuple(cfg.get("terms", DEFAULT_TERMS)) + tuple(cfg.get("extra_terms", ()))
    budget = [int(cfg.get("max_messages_per_tick", MAX_PAGES))]
    changes = []
    for row in rows:
        if row["id"] in seen:
            continue
        verdict = None if seeding else _judge(row, deps, terms, budget, detail)
        if verdict is False:
            continue  # not marked seen: judged next tick
        seen.append(row["id"])
        if verdict:
            changes.append(change("rats", NAME, term=verdict, date=row["date"], subject=row["subject"],
                                  url=f"{ARCHIVE}/arch/msg/rats/{row['id']}/"))
    sl["rats_seen_ids"] = cap(seen)
    detail["rats_rows"] = len(rows)
    detail["rats_pages"] = int(cfg.get("max_messages_per_tick", MAX_PAGES)) - budget[0]
    return changes


def collect(slice_: dict, deps: IetfDeps, *, now: datetime, config: dict) -> Result:
    cfg = config["ietf"]
    sl = dict(slice_)
    detail: dict = {}
    draft_changes, skip = _draft(sl, deps, cfg, detail)
    if skip and str(skip).startswith(("network", "timeout")):
        return Result(dict(slice_), [], skipped=skip, detail=detail)
    if skip:
        detail["draft"] = skip
    changes = draft_changes + _rats(sl, deps, cfg, detail)
    return Result(sl, changes, None, detail)


def describe(result: Result) -> str:
    if result.skipped:
        return f"skipped: {result.skipped}"
    sl = result.slice
    rev = sl.get("draft_rev")
    rev_text = f"rev={int(rev):02d}" if rev is not None else "rev unknown"
    return f"{rev_text}, {len(sl.get('rats_seen_ids', []))} rats ids"


# ---------------------------------------------------------------------------
# Real and fixture I/O
# ---------------------------------------------------------------------------
def default_deps(config: dict) -> IetfDeps:
    return IetfDeps(http=fetch.http, pacer=Pacer(1.0))


def fixture_deps(directory: Path) -> IetfDeps:
    return IetfDeps(http=fixture_http(directory), pacer=Pacer(1.0, sleep=lambda s: None))
