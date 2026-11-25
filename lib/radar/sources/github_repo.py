"""The GitHub watch over the CodeTonight-SA repositories named in ``config/radar.json``.

Everything goes through ``gh api`` and the CLI's existing login (about 19 core
calls per tick, about 900 a day of the 5000-an-hour limit). Immediate: an issue or
pull request opened by someone other than the operator (the gh login's own
account, read once from ``gh api user`` and cached in the slice, plus any
``operator_logins`` from the gitignored local config; the committed config names
nobody), and a notification whose reason is ``mention`` or ``team_mention`` (measured 2026-10-21: 0 of 100
participating notifications carried either reason, so this is quiet by default).
Roll-up: new stargazers with their logins, and the previous UTC day's views and
uniques with the top referrer from the traffic API (``gh`` has push access on all
three repos, measured 2026-10-20). A 403 on a traffic endpoint skips traffic for
that repo only; stars and issues are still diffed.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

from .. import fetch
from ..fetch import Fetched
from ..fixtures import fixture_gh
from ..model import ROLLUP, Result, change
from ..schedule import iso, parse_iso
from ..state import cap

NAME = "github_repo"
STAR_ACCEPT = "application/vnd.github.star+json"
PER_PAGE = 100
MAX_STAR_PAGES = 10
MENTION_REASONS = {"mention", "team_mention"}
FATAL_SKIPS = ("gh auth", "gh not installed", "network", "timeout")
TRAFFIC_LEAVES = ("views", "clones", "popular/referrers", "popular/paths")
TRAFFIC_KEYS = ("views_14d", "uniques_14d", "views_days", "clones_14d", "clones_uniques_14d",
                "referrers", "paths")


FIXTURE_VIEWER = "v-example"


@dataclass
class GitHubDeps:
    gh: Callable[..., Fetched]                              # gh(path, accept=None) -> Fetched
    viewer_login: Callable[[], str | None] = lambda: None  # the gh login's own account, read once


def repo_short(repo: str) -> str:
    return repo.split("/", 1)[-1]


def html_url(api_url: str) -> str:
    """``https://api.github.com/repos/o/r/pulls/8`` -> ``https://github.com/o/r/pull/8``."""
    url = str(api_url or "").replace("https://api.github.com/repos/", "https://github.com/")
    return url.replace("/pulls/", "/pull/")


def _is_fatal(skip: str | None) -> bool:
    return bool(skip) and str(skip).startswith(FATAL_SKIPS)


def _repo_fields(data: dict) -> dict:
    return {
        "stars": int(data.get("stargazers_count") or 0),
        "forks": int(data.get("forks_count") or 0),
        "watchers": int(data.get("subscribers_count") or 0),
        "open_issues": int(data.get("open_issues_count") or 0),
        "html_url": str(data.get("html_url") or ""),
    }


def _stargazers(deps: GitHubDeps, repo: str, prev: dict, stars: int, detail: dict) -> tuple[list[str] | None, list[dict]]:
    logins: list[str] = []
    pages = min(MAX_STAR_PAGES, max(1, -(-stars // PER_PAGE)))
    for page in range(1, pages + 1):
        res = deps.gh(f"repos/{repo}/stargazers?per_page={PER_PAGE}&page={page}", STAR_ACCEPT)
        if not res.ok:
            detail[f"{repo} stargazers"] = res.skip
            return None, []  # unknown this tick: the caller keeps the old list, or has no baseline yet
        rows = res.json() or []
        logins.extend(str((row.get("user") or {}).get("login") or "") for row in rows if isinstance(row, dict))
    logins = [login for login in logins if login]
    known = prev.get("stargazers")
    if known is None:  # first sight of this repo's stars is their baseline: nothing to report
        return logins, []
    known = set(known)
    return logins, [change("star", NAME, ROLLUP, repo=repo, login=login) for login in logins if login not in known]


def _issue_change(repo: str, row: dict, since: datetime | None, operator: set[str]) -> dict | None:
    login = str((row.get("user") or {}).get("login") or "")
    if login.lower() in operator:
        return None
    created = parse_iso(row.get("created_at"))
    if since is not None and created is not None and created <= since:
        return None  # updated since the last run, not opened since it
    kind = "pr" if "pull_request" in row else "issue"
    return change(kind, NAME, repo=repo, repo_short=repo_short(repo), number=row.get("number"),
                  login=login, title=str(row.get("title") or ""), url=str(row.get("html_url") or ""))


def _issues(deps: GitHubDeps, repo: str, prev: dict, fallback_since: datetime | None, operator: set[str],
            detail: dict, now: datetime) -> tuple[list[int], list[dict], str | None]:
    """(known issue numbers, changes, the issues watermark). The watermark moves
    only on a successful fetch, so an issue opened while one request failed is
    still inside ``since`` on the next tick (Codex on GRIP#8633)."""
    since = parse_iso(prev.get("issues_since")) or fallback_since
    path = f"repos/{repo}/issues?state=all&per_page=50" + (f"&since={iso(since)}" if since else "")
    res = deps.gh(path)
    known = list(prev.get("issue_numbers", []))
    if not res.ok:
        detail[f"{repo} issues"] = res.skip
        return known, [], prev.get("issues_since")
    numbers, changes = set(known), []
    for row in res.json() or []:
        number = row.get("number") if isinstance(row, dict) else None
        if not isinstance(number, int):
            continue
        is_new = number not in numbers
        numbers.add(number)
        if not prev or not is_new:
            continue
        found = _issue_change(repo, row, since, operator)
        if found:
            changes.append(found)
    return cap(sorted(numbers)), changes, iso(now)


def _views_fields(data) -> dict:
    if not isinstance(data, dict):
        return {}
    days = [{"day": str(d.get("timestamp") or "")[:10], "count": int(d.get("count") or 0),
             "uniques": int(d.get("uniques") or 0)} for d in data.get("views", []) if isinstance(d, dict)]
    return {"views_14d": int(data.get("count") or 0), "uniques_14d": int(data.get("uniques") or 0),
            "views_days": days}


def _clones_fields(data) -> dict:
    if not isinstance(data, dict):
        return {}
    return {"clones_14d": int(data.get("count") or 0), "clones_uniques_14d": int(data.get("uniques") or 0)}


def _top5(data, key: str, name: str) -> dict:
    if not isinstance(data, list):
        return {}
    return {name: [{key: r.get(key), "count": r.get("count"), "uniques": r.get("uniques")}
                   for r in data[:5] if isinstance(r, dict)]}


_TRAFFIC_PARSERS = {
    "views": _views_fields,
    "clones": _clones_fields,
    "popular/referrers": lambda d: _top5(d, "referrer", "referrers"),
    "popular/paths": lambda d: _top5(d, "path", "paths"),
}


def _traffic(deps: GitHubDeps, repo: str, prev: dict, detail: dict) -> dict:
    out = {k: prev[k] for k in TRAFFIC_KEYS if k in prev}  # a leaf that fails keeps its last good numbers
    for leaf in TRAFFIC_LEAVES:
        res = deps.gh(f"repos/{repo}/traffic/{leaf}")
        if not res.ok:
            detail[f"{repo} traffic/{leaf}"] = res.skip
            continue
        out.update(_TRAFFIC_PARSERS[leaf](res.json()))
    return out


def _mentions(deps: GitHubDeps, seen: list[str], detail: dict) -> tuple[list[str], list[dict]]:
    res = deps.gh("notifications?participating=true&per_page=50")
    if not res.ok:
        detail["notifications"] = res.skip
        return seen, []
    seen_set, new_ids, changes = set(seen), [], []
    for row in res.json() or []:
        if not isinstance(row, dict) or row.get("reason") not in MENTION_REASONS:
            continue
        nid = str(row.get("id") or "")
        if not nid or nid in seen_set:
            continue
        new_ids.append(nid)
        subject = row.get("subject") or {}
        changes.append(change("mention", NAME, repo=str((row.get("repository") or {}).get("full_name") or ""),
                              title=str(subject.get("title") or ""), url=html_url(subject.get("url") or "")))
    return cap(list(seen) + new_ids), changes


def collect(slice_: dict, deps: GitHubDeps, *, now: datetime, config: dict) -> Result:
    cfg = config["github"]
    sl = dict(slice_)
    repos = dict(sl.get("repos", {}))
    changes: list[dict] = []
    detail: dict = {}
    operator = {login.lower() for login in cfg.get("operator_logins", [])}
    viewer = sl.get("viewer_login") or deps.viewer_login()
    if viewer:
        sl["viewer_login"] = viewer  # cached: one gh call for the life of the state file
        operator.add(str(viewer).lower())
    since = parse_iso(slice_.get("last_run"))
    for repo in cfg["repos"]:
        res = deps.gh(f"repos/{repo}")
        if _is_fatal(res.skip):
            return Result(dict(slice_), [], skipped=res.skip, detail=detail)
        if not res.ok:
            detail[repo] = f"repo: {res.skip}"
            continue
        prev = repos.get(repo, {})
        cur = _repo_fields(res.json() or {})
        stars, star_changes = _stargazers(deps, repo, prev, cur["stars"], detail)
        if stars is not None:
            cur["stargazers"] = stars
        elif "stargazers" in prev:
            cur["stargazers"] = prev["stargazers"]
        cur["issue_numbers"], issue_changes, cur["issues_since"] = _issues(deps, repo, prev, since, operator, detail, now)
        cur.update(_traffic(deps, repo, prev, detail))
        repos[repo] = cur
        changes += star_changes + issue_changes
    sl["repos"] = repos
    sl["seen_notification_ids"], mention_changes = _mentions(deps, list(sl.get("seen_notification_ids", [])), detail)
    changes += mention_changes
    return Result(sl, changes, None, detail)


def describe(result: Result) -> str:
    if result.skipped:
        return f"skipped: {result.skipped}"
    parts = []
    for repo, r in result.slice.get("repos", {}).items():
        counts = " ".join(_plural(r.get(key, 0), noun) for key, noun in
                          (("stars", "star"), ("forks", "fork"), ("watchers", "watcher"), ("open_issues", "issue")))
        parts.append(f"{repo_short(repo)} {counts}, views {r.get('views_14d', '?')}/{r.get('uniques_14d', '?')}")
    return "; ".join(parts) or "no repos"


def _plural(n, noun: str) -> str:
    return f"{n} {noun}{'' if n == 1 else 's'}"


def rollup_bits(repo: str, r: dict, star_logins: list[str], day: str, label: str) -> str | None:
    """One repo's clause for the 08:00 line, or None when it moved nowhere."""
    bits = []
    if star_logins:
        n = len(star_logins)
        bits.append(f"+{n} star{'' if n == 1 else 's'} ({', '.join('@' + login for login in star_logins)})")
    views = next((d for d in r.get("views_days", []) if d.get("day") == day), None)
    if views and views.get("count"):
        top = ((r.get("referrers") or [{}])[0] or {}).get("referrer")
        noun = "view" if views["count"] == 1 else "views"
        bits.append(f"{views['count']} {noun}/{views['uniques']} uniq" + (f" top {top}" if top else ""))
    return f"{label} " + ", ".join(bits) if bits else None


# ---------------------------------------------------------------------------
# Real and fixture I/O
# ---------------------------------------------------------------------------
def _viewer_login() -> str | None:
    """The login behind gh's own token (``gh api user``): the operator's primary
    account, excluded from "someone else opened this" without being written to a
    committed file (tests/test_no_hardcoded_operator_paths.py)."""
    res = fetch.gh("user")
    data = res.json() if res.ok else None
    login = data.get("login") if isinstance(data, dict) else None
    return str(login) if login else None


def default_deps(config: dict) -> GitHubDeps:
    return GitHubDeps(gh=fetch.gh, viewer_login=_viewer_login)


def fixture_deps(directory: Path) -> GitHubDeps:
    """``gh.json`` maps an API path (query string stripped) to its JSON body;
    its ``__status__`` object maps a path to a failing HTTP status
    (``lib/radar/fixtures.py``, shared with the web-mention source)."""
    return GitHubDeps(gh=fixture_gh(directory), viewer_login=lambda: FIXTURE_VIEWER)
