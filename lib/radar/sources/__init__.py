"""The source registry. Run order is dict order: the reply watch first, because
it is the time-critical one.

Each source module exposes the same surface:

- ``NAME``
- ``collect(slice, deps, *, now, config) -> model.Result``
- ``default_deps(config)``: the real I/O
- ``fixture_deps(directory)``: the same I/O read from JSON files, for the tests
  and for ``RADAR_FIXTURES=<dir>`` on the command line
- ``describe(result) -> str``: one clause for the seed line
"""
from __future__ import annotations

from . import github_repo, ietf, mail_replies, mentions, search_console, vercel_site

SOURCES = {
    mail_replies.NAME: mail_replies,
    github_repo.NAME: github_repo,
    ietf.NAME: ietf,
    mentions.NAME: mentions,
    vercel_site.NAME: vercel_site,
    search_console.NAME: search_console,
}

__all__ = ["SOURCES", "github_repo", "ietf", "mail_replies", "mentions", "search_console", "vercel_site"]
