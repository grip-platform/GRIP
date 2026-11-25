"""Fixture I/O for the tests and for ``RADAR_FIXTURES=<dir>`` on the command line.

``http.json`` in the directory maps a URL (scheme, host and path; the query
string is stripped before the lookup) to either a file name in the same
directory or an inline JSON body. ``gh.json`` maps a ``gh api`` path the same
way. Each table's ``__status__`` object maps a key to a failing HTTP status.
Anything not listed answers 404, so a source skips instead of reaching the
network, and a directory without a table answers 404 to everything.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

from .fetch import Fetched, skip_for_status


def _table(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}


def _body(directory: Path, value) -> str:
    if isinstance(value, str) and (directory / value).is_file():
        return (directory / value).read_text(encoding="utf-8")
    return value if isinstance(value, str) else json.dumps(value)


def answer(directory: Path, table: dict, key: str) -> Fetched:
    statuses = table.get("__status__") or {}
    if key in statuses:
        status = int(statuses[key])
        return Fetched(status, "", skip_for_status(status))
    if key not in table:
        return Fetched(404, "", "404")
    return Fetched(200, _body(directory, table[key]))


def fixture_http(directory: Path) -> Callable[..., Fetched]:
    """An ``http(url, **kwargs)`` that answers from ``http.json``."""
    directory = Path(directory)
    table = _table(directory / "http.json")

    def http(url: str, **kwargs) -> Fetched:
        return answer(directory, table, url.split("?", 1)[0])

    return http


def fixture_gh(directory: Path) -> Callable[..., Fetched]:
    """A ``gh(path, accept=None)`` that answers from ``gh.json``."""
    directory = Path(directory)
    table = _table(directory / "gh.json")

    def gh(path: str, accept: str | None = None) -> Fetched:
        return answer(directory, table, path.split("?", 1)[0])

    return gh
