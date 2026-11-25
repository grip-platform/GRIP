"""Mentions on the web: Hacker News, GitHub search, Reddit, Bluesky and Google
Alerts RSS feeds, for the terms in ``config/radar.json`` (``mentions.terms``,
plus ``mentions.extra_terms`` from the per-machine file, where a person's name
belongs rather than in the committed defaults).

Every hit is pushed at once as a ``web`` line. Ids are kept per channel in the
slice; a channel seen for the first time only records what exists, so a channel
added later never alerts on its backlog.

Hacker News answers anonymously (Algolia ``search_by_date``). GitHub goes
through ``gh api search/...`` two seconds apart; code search is skipped on 403.
Reddit and Bluesky both answer 403 to anonymous calls from this Mac (probed
2026-10-21), so each is a per-channel skip until the keychain holds
``grip-reddit`` (``client_id:secret``, OAuth client credentials) or
``grip-bluesky`` (``handle:app-password``, an app session). A network failure
anywhere makes the whole source wait for the next tick.
"""
from __future__ import annotations

import base64
import itertools
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qs, urlencode, urlparse
from xml.etree import ElementTree

from .. import fetch
from ..fetch import Fetched, Pacer
from ..fixtures import fixture_gh, fixture_http
from ..model import Result, change
from ..state import cap
from . import github_repo

NAME = "mentions"
HN = "https://hn.algolia.com/api/v1/search_by_date"
GH_SEARCH = (("repo", "search/repositories"), ("issue", "search/issues"), ("code", "search/code"))
REDDIT_SEARCH = "https://www.reddit.com/search.json"
REDDIT_TOKEN = "https://www.reddit.com/api/v1/access_token"
REDDIT_OAUTH_SEARCH = "https://oauth.reddit.com/search"
BSKY_PUBLIC = "https://public.api.bsky.app/xrpc/app.bsky.feed.searchPosts"
BSKY_SESSION = "https://bsky.social/xrpc/com.atproto.server.createSession"
BSKY_SEARCH = "https://bsky.social/xrpc/app.bsky.feed.searchPosts"
LABELS = {"hn": "HN", "github": "GitHub", "reddit": "Reddit", "bluesky": "Bluesky", "rss": "Google Alert"}
FATAL_PREFIXES = ("network", "timeout", "gh auth", "gh not installed")


class Fatal(Exception):
    """The whole source waits for the next tick (network down, gh logged out)."""


@dataclass
class MentionDeps:
    http: Callable[..., Fetched]
    gh: Callable[..., Fetched]
    secret: Callable[[str], str | None]   # keychain service -> value, or None
    gh_pacer: Pacer
    reddit_pacer: Pacer
    viewer_login: Callable[[], str | None] = lambda: None  # the gh login's own account, as the GitHub source reads it


def display_term(term) -> str:
    return str(term).strip().strip('"')


def _hit(term: str, what: str, url: str, title, hit_id: str | None = None) -> dict:
    return {"id": hit_id or url, "term": term, "what": what, "url": url, "title": str(title or "")}


def _get(deps: MentionDeps, url: str, headers: dict | None = None, data: bytes | None = None) -> Fetched:
    res = deps.http(url, headers=headers, data=data)
    if not res.ok and str(res.skip).startswith(FATAL_PREFIXES):
        raise Fatal(str(res.skip))
    return res


def _notes(prefix: str, notes: list[str]) -> str | None:
    return f"{prefix}: " + "; ".join(sorted(set(notes))) if notes else None


# ---------------------------------------------------------------------------
# Hacker News
# ---------------------------------------------------------------------------
def _hn_hits(res: Fetched, term: str) -> list[dict]:
    hits = []
    for h in (res.json() or {}).get("hits") or []:
        oid = str(h.get("objectID") or "")
        if oid:
            what = "comment" if "comment" in (h.get("_tags") or []) else "story"
            hits.append(_hit(term, what, f"https://news.ycombinator.com/item?id={oid}",
                             h.get("title") or h.get("story_title"), oid))
    return hits


def _hn(deps: MentionDeps, terms: list[str], cfg: dict) -> tuple[list[dict], str | None]:
    hits, notes = [], []
    for term in terms:
        res = _get(deps, f"{HN}?{urlencode({'query': term, 'tags': '(story,comment)', 'hitsPerPage': 20})}")
        if res.ok:
            hits += _hn_hits(res, term)
        else:
            notes.append(str(res.skip))
    return hits, _notes("hn", notes)


# ---------------------------------------------------------------------------
# GitHub search
# ---------------------------------------------------------------------------
def _gh_owner(item: dict, kind: str) -> str:
    if kind == "repo":
        return str((item.get("owner") or {}).get("login") or "")
    if kind == "code":
        return str(((item.get("repository") or {}).get("owner") or {}).get("login") or "")
    return str(item.get("repository_url") or "").rsplit("/repos/", 1)[-1].split("/", 1)[0]


def _gh_hits(res: Fetched, kind: str, term: str, exclude: set[str]) -> list[dict]:
    hits = []
    for item in (res.json() or {}).get("items") or []:
        url = str(item.get("html_url") or "")
        if url and _gh_owner(item, kind).lower() not in exclude:
            hits.append(_hit(term, kind, url, item.get("full_name") or item.get("title") or item.get("name")))
    return hits


def _github(deps: MentionDeps, terms: list[str], cfg: dict) -> tuple[list[dict], str | None]:
    exclude = {str(o).lower() for o in list(cfg.get("exclude_owners", [])) + list(cfg.get("_own_logins", []))}
    hits, notes = [], []
    for term, (kind, path) in itertools.product(terms, GH_SEARCH):
        deps.gh_pacer.wait()
        res = deps.gh(f"{path}?{urlencode({'q': term, 'per_page': 20})}")
        if res.ok:
            hits += _gh_hits(res, kind, term, exclude)
        elif str(res.skip).startswith(FATAL_PREFIXES):
            raise Fatal(str(res.skip))
        else:
            notes.append(f"{kind}: {res.skip}")
    return hits, _notes("github", notes)


# ---------------------------------------------------------------------------
# Reddit
# ---------------------------------------------------------------------------
def _reddit_token(deps: MentionDeps, creds: str) -> str | None:
    basic = base64.b64encode(creds.encode("utf-8")).decode("ascii")
    res = _get(deps, REDDIT_TOKEN, headers={"Authorization": f"Basic {basic}"}, data=b"grant_type=client_credentials")
    token = (res.json() or {}).get("access_token") if res.ok else None
    return str(token) if token else None


def _reddit_hits(res: Fetched, term: str) -> list[dict]:
    hits = []
    for child in ((res.json() or {}).get("data") or {}).get("children") or []:
        d = child.get("data") or {}
        if d.get("id") and d.get("permalink"):
            hits.append(_hit(term, "post", f"https://www.reddit.com{d['permalink']}", d.get("title"), str(d["id"])))
    return hits


def _reddit(deps: MentionDeps, terms: list[str], cfg: dict) -> tuple[list[dict], str | None]:
    creds = deps.secret("grip-reddit")
    token = _reddit_token(deps, creds) if creds and ":" in creds else None
    if creds and token is None:
        return [], "reddit: credentials present but no token was issued"
    base, headers = (REDDIT_OAUTH_SEARCH, {"Authorization": f"bearer {token}"}) if token else (REDDIT_SEARCH, None)
    hits, notes = [], []
    for term in terms:
        deps.reddit_pacer.wait()
        res = _get(deps, f"{base}?{urlencode({'q': term, 'sort': 'new', 'limit': 25, 't': 'week'})}", headers=headers)
        if res.ok:
            hits += _reddit_hits(res, term)
        else:
            notes.append(str(res.skip))
    return hits, _notes("reddit", notes)


# ---------------------------------------------------------------------------
# Bluesky
# ---------------------------------------------------------------------------
def _bsky_session(deps: MentionDeps, creds: str) -> str | None:
    handle, _, password = creds.partition(":")
    body = json.dumps({"identifier": handle, "password": password}).encode("utf-8")
    res = _get(deps, BSKY_SESSION, headers={"Content-Type": "application/json"}, data=body)
    jwt = (res.json() or {}).get("accessJwt") if res.ok else None
    return str(jwt) if jwt else None


def _bsky_hits(res: Fetched, term: str) -> list[dict]:
    hits = []
    for post in (res.json() or {}).get("posts") or []:
        uri = str(post.get("uri") or "")
        handle = str((post.get("author") or {}).get("handle") or "")
        if uri and handle:
            url = f"https://bsky.app/profile/{handle}/post/{uri.rsplit('/', 1)[-1]}"
            hits.append(_hit(term, "post", url, str((post.get("record") or {}).get("text") or "")[:80], uri))
    return hits


def _bluesky(deps: MentionDeps, terms: list[str], cfg: dict) -> tuple[list[dict], str | None]:
    creds = deps.secret("grip-bluesky")
    jwt = _bsky_session(deps, creds) if creds and ":" in creds else None
    if creds and jwt is None:
        return [], "bluesky: credentials present but no session was created"
    base, headers = (BSKY_SEARCH, {"Authorization": f"Bearer {jwt}"}) if jwt else (BSKY_PUBLIC, None)
    hits, notes = [], []
    for term in terms:
        res = _get(deps, f"{base}?{urlencode({'q': term, 'sort': 'latest', 'limit': 25})}", headers=headers)
        if res.ok:
            hits += _bsky_hits(res, term)
        else:
            notes.append(str(res.skip))
    return hits, _notes("bluesky", notes)


# ---------------------------------------------------------------------------
# Google Alerts (RSS / Atom)
# ---------------------------------------------------------------------------
def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def unwrap(link: str) -> str:
    """Google Alerts wraps the target in ``google.com/url?...&url=<target>``."""
    parsed = urlparse(link)
    if parsed.netloc.endswith("google.com") and parsed.path == "/url":
        return parse_qs(parsed.query).get("url", [""])[0] or link
    return link


def _entry(node) -> dict:
    out = {"id": "", "title": "", "link": ""}
    for child in node:
        name, text = _local(child.tag), (child.text or "").strip()
        if name in ("id", "guid"):
            out["id"] = text
        elif name == "title":
            out["title"] = text
        elif name == "link":
            out["link"] = unwrap(child.get("href") or text)
    return out


def parse_feed(xml_text: str) -> tuple[str, list[dict]]:
    """``(feed title, [{id, title, link}])`` for Atom entries and RSS items."""
    try:
        root = ElementTree.fromstring(xml_text)
    except ElementTree.ParseError:
        return "", []
    title, entries = "", []
    for node in root.iter():
        name = _local(node.tag)
        if name == "title" and not title and node.text:
            title = node.text.strip()  # the feed's own title comes before any entry
        elif name in ("entry", "item"):
            entries.append(_entry(node))
    return title, [e for e in entries if e["id"] and e["link"]]


def _rss(deps: MentionDeps, terms: list[str], cfg: dict) -> tuple[list[dict], str | None]:
    hits, notes = [], []
    for feed in cfg.get("alerts_rss", []):
        res = _get(deps, feed)
        if not res.ok:
            notes.append(f"{feed}: {res.skip}")
            continue
        title, entries = parse_feed(res.body)
        term = title.split(" - ", 1)[1] if " - " in title else (title or "alert")
        hits += [_hit(term, "hit", e["link"], e["title"], e["id"]) for e in entries]
    return hits, _notes("rss", notes)


CHANNELS = {"hn": _hn, "github": _github, "reddit": _reddit, "bluesky": _bluesky, "rss": _rss}


# ---------------------------------------------------------------------------
# The source
# ---------------------------------------------------------------------------
def _record(channel: str, hits: list[dict], seen: dict) -> list[dict]:
    """New hits become ``web`` changes; a channel's first sight only records ids."""
    first = channel not in seen
    ids = seen.setdefault(channel, [])
    known, out = set(ids), []
    for hit in hits:
        if hit["id"] in known:
            continue
        known.add(hit["id"])
        ids.append(hit["id"])
        if not first:
            out.append(change("web", NAME, channel=LABELS[channel], what=hit["what"],
                              term=display_term(hit["term"]), url=hit["url"], title=hit["title"]))
    return out


def _own_logins(deps: MentionDeps, sl: dict, config: dict) -> list[str]:
    """The operator's GitHub logins, so a search never reports his own repositories
    and issues: ``github.operator_logins`` from the local config plus the gh
    login's own account (``gh api user``, read once and kept in the slice, the
    same reader the GitHub source uses). The committed config names nobody."""
    logins = [str(x) for x in config.get("github", {}).get("operator_logins", [])]
    viewer = sl.get("viewer_login") or deps.viewer_login()
    if viewer:
        sl["viewer_login"] = str(viewer)
        logins.append(str(viewer))
    return logins


def collect(slice_: dict, deps: MentionDeps, *, now: datetime, config: dict) -> Result:
    sl = dict(slice_)
    seen = {k: list(v) for k, v in (sl.get("seen") or {}).items()}
    detail: dict = {}
    changes: list[dict] = []
    cfg = dict(config.get("mentions", {}))
    cfg["_own_logins"] = _own_logins(deps, sl, config)
    terms = [t for t in list(cfg.get("terms", [])) + list(cfg.get("extra_terms", []))
             if isinstance(t, str) and t.strip()]
    try:
        for channel in cfg.get("channels", list(CHANNELS)):
            fn = CHANNELS.get(channel)
            if fn is None:
                detail[channel] = "unknown channel"
                continue
            hits, note = fn(deps, terms, cfg)
            if note:
                detail[channel] = note
            changes += _record(channel, hits, seen)
    except Fatal as exc:
        return Result(dict(slice_), [], skipped=str(exc), detail=detail)
    sl["seen"] = {k: cap(v) for k, v in seen.items()}
    detail["ids"] = {k: len(v) for k, v in sl["seen"].items()}
    return Result(sl, changes, None, detail)


def describe(result: Result) -> str:
    if result.skipped:
        return f"skipped: {result.skipped}"
    ids = result.detail.get("ids", {})
    parts = []
    for ch in LABELS:
        n = ids.get(ch, 0)
        parts.append(f"{ch}={n} id{'' if n == 1 else 's'}" + (f" ({result.detail[ch]})" if ch in result.detail else ""))
    return ", ".join(parts)


# ---------------------------------------------------------------------------
# Real and fixture I/O
# ---------------------------------------------------------------------------
def default_deps(config: dict) -> MentionDeps:
    def secret(service: str) -> str | None:
        from provider_secret import resolve_provider_key
        return resolve_provider_key(service)

    return MentionDeps(http=fetch.http, gh=fetch.gh, secret=secret, gh_pacer=Pacer(2.0), reddit_pacer=Pacer(1.0),
                       viewer_login=github_repo.default_deps(config).viewer_login)


def fixture_deps(directory: Path) -> MentionDeps:
    quiet = Pacer(0.0, sleep=lambda s: None)
    return MentionDeps(http=fixture_http(directory), gh=fixture_gh(directory), secret=lambda service: None,
                       gh_pacer=quiet, reddit_pacer=quiet, viewer_login=github_repo.fixture_deps(directory).viewer_login)
