"""GRIP changelog detection + state tracking.

Single source of truth for "is there new content since the operator last saw
the changelog?" Both `bin/grip` splash and the SessionStart hook delegate
here so they stay in sync.

State file format (`state/changelog/last-shown.json`)::

    {
      "shown_through_date": "2026-04-27",
      "file_hash": "sha256:abcd...",
      "last_viewed_at": "2026-04-28T09:00:00Z"
    }

CHANGELOG.md format expectations (Keep-a-Changelog with date headers)::

    ## 2026-04-28
    ### Features
    - one bullet
    ### Fixes
    - another bullet

Public API: ``has_new_content``, ``new_entries``, ``summarise``, ``render``,
``mark_seen``. CLI subcommands: ``status``, ``show``, ``mark-seen``.

Falsifier: this module is wrong if the parser misses any date-headed
section in the live CHANGELOG.md, or if ``mark_seen`` fails to persist the
state file. Both covered by ``tests/test_grip_changelog.py``.
"""
from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import os
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", str(Path.home() / ".claude")))
DEFAULT_CHANGELOG = CLAUDE_DIR / "CHANGELOG.md"
DEFAULT_STATE = CLAUDE_DIR / "state" / "changelog" / "last-shown.json"

_DATE_HEADER = re.compile(r"^##\s+(\d{4}-\d{2}-\d{2})\s*$")
_SECTION_HEADER = re.compile(r"^###\s+(.+?)\s*$")
_BULLET = re.compile(r"^-\s+(.+?)\s*$")


@dataclass
class Entry:
    """One date-headed CHANGELOG entry."""

    date: str
    sections: dict[str, list[str]] = field(default_factory=dict)

    @property
    def total_items(self) -> int:
        return sum(len(items) for items in self.sections.values())


def _file_hash(path: Path) -> str:
    """SHA-256 of file contents, prefixed with algorithm name."""
    h = hashlib.sha256(path.read_bytes()).hexdigest()
    return f"sha256:{h}"


def parse(changelog_path: Path = DEFAULT_CHANGELOG) -> list[Entry]:
    """Parse CHANGELOG.md into a list of entries (newest first preserved)."""
    if not changelog_path.exists():
        return []

    entries: list[Entry] = []
    current: Entry | None = None
    current_section: str | None = None

    for raw_line in changelog_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.rstrip()

        date_match = _DATE_HEADER.match(line)
        if date_match:
            current = Entry(date=date_match.group(1))
            entries.append(current)
            current_section = None
            continue

        if current is None:
            continue

        section_match = _SECTION_HEADER.match(line)
        if section_match:
            current_section = section_match.group(1)
            current.sections.setdefault(current_section, [])
            continue

        bullet_match = _BULLET.match(line)
        if bullet_match and current_section is not None:
            current.sections[current_section].append(bullet_match.group(1))

    return entries


def _load_state(state_path: Path = DEFAULT_STATE) -> dict:
    """Load persisted last-shown state. Empty dict on first run or corruption."""
    if not state_path.exists():
        return {}
    try:
        return json.loads(state_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _save_state(state: dict, state_path: Path = DEFAULT_STATE) -> None:
    """Persist state, creating parent dirs as needed."""
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")


def new_entries(
    changelog_path: Path = DEFAULT_CHANGELOG,
    state_path: Path = DEFAULT_STATE,
) -> list[Entry]:
    """Return entries newer than the operator's last-seen date.

    First run: returns empty list (no spam on initial install) — sets a
    seen-pointer to current head silently.
    """
    entries = parse(changelog_path)
    if not entries:
        return []

    state = _load_state(state_path)
    shown_through = state.get("shown_through_date")

    if shown_through is None:
        return []

    return [e for e in entries if e.date > shown_through]


def has_new_content(
    changelog_path: Path = DEFAULT_CHANGELOG,
    state_path: Path = DEFAULT_STATE,
) -> bool:
    """True iff there's at least one entry newer than last seen."""
    return len(new_entries(changelog_path, state_path)) > 0


def summarise(entries: list[Entry]) -> str:
    """Plain-English count summary, e.g. '6 features, 3 fixes, 2 docs'.

    Per Rule 17: no GRIP jargon. Section names lowercased.
    """
    if not entries:
        return ""

    counts: Counter[str] = Counter()
    pairs = itertools.chain.from_iterable(e.sections.items() for e in entries)
    for section, items in pairs:
        counts[section.lower()] += len(items)

    if not counts:
        n = len(entries)
        return f"{n} new entr{'y' if n == 1 else 'ies'}"

    parts = [
        f"{count} {label}"
        for label, count in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    ]
    return ", ".join(parts)


def _render_section(name: str, items: list[str], width: int) -> list[str]:
    """Render one ### subsection as terminal lines."""
    head = [f"    {name}:"]
    head.extend(_wrap(item, indent=6, width=width) for item in items)
    return head


def _render_entry(entry: Entry, width: int) -> list[str]:
    """Render one date-headed entry as terminal lines."""
    header = [f"  {entry.date}", f"  {'-' * len(entry.date)}"]
    body_chunks = (
        _render_section(name, items, width)
        for name, items in entry.sections.items()
    )
    body = list(itertools.chain.from_iterable(body_chunks))
    return header + body + [""]


def render(entries: list[Entry], width: int = 76) -> str:
    """Render entries as a human-readable terminal block."""
    if not entries:
        return ""
    chunks = (_render_entry(e, width) for e in entries)
    flat = list(itertools.chain.from_iterable(chunks))
    return "\n".join(flat).rstrip()


def _wrap(text: str, indent: int, width: int) -> str:
    """Word-wrap a bullet to fit terminal width, indented."""
    pad = " " * indent
    bullet_pad = pad + "- "
    cont_pad = pad + "  "
    words = text.split()
    if not words:
        return ""

    lines: list[str] = []
    current = bullet_pad + words[0]
    for word in words[1:]:
        if len(current) + 1 + len(word) <= width:
            current += " " + word
        else:
            lines.append(current)
            current = cont_pad + word
    lines.append(current)
    return "\n".join(lines)


def mark_seen(
    changelog_path: Path = DEFAULT_CHANGELOG,
    state_path: Path = DEFAULT_STATE,
    through_date: str | None = None,
) -> str | None:
    """Update state to mark entries as seen.

    If through_date is None: marks up to the newest entry in the file.
    Returns the date that was marked, or None if changelog is empty.
    """
    entries = parse(changelog_path)
    if not entries:
        return None

    head_date = max(e.date for e in entries)
    target = through_date if through_date else head_date

    state = {
        "shown_through_date": target,
        "file_hash": _file_hash(changelog_path),
        "last_viewed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    _save_state(state, state_path)
    return target


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _cmd_status(args: argparse.Namespace) -> int:
    """Print JSON status: has_new + summary + count, for shell consumers."""
    entries = new_entries(Path(args.changelog), Path(args.state))
    payload = {
        "has_new": len(entries) > 0,
        "count": len(entries),
        "summary": summarise(entries),
        "newest_date": entries[0].date if entries else None,
    }
    print(json.dumps(payload))
    return 0


def _cmd_show(args: argparse.Namespace) -> int:
    """Render the changelog (default: new entries; --all: full file)."""
    if args.all:
        entries = parse(Path(args.changelog))
    elif args.since:
        all_entries = parse(Path(args.changelog))
        entries = [e for e in all_entries if e.date > args.since]
    else:
        entries = new_entries(Path(args.changelog), Path(args.state))

    if not entries:
        if args.all:
            print("CHANGELOG.md is empty.")
        else:
            print("No new changelog entries since you last viewed.")
            print("Run with --all to see the full changelog.")
        return 0

    print(render(entries))
    if not args.no_mark and not args.all and not args.since:
        mark_seen(Path(args.changelog), Path(args.state))
    return 0


def _cmd_mark_seen(args: argparse.Namespace) -> int:
    """Mark entries as seen up to a date (or to head if omitted)."""
    marked = mark_seen(Path(args.changelog), Path(args.state), args.through)
    if marked:
        print(f"Marked seen through {marked}")
        return 0
    print("CHANGELOG.md is empty — nothing to mark.")
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="GRIP changelog detection + viewer")
    parser.add_argument("--changelog", default=str(DEFAULT_CHANGELOG))
    parser.add_argument("--state", default=str(DEFAULT_STATE))
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status", help="JSON status (used by splash)")

    show = sub.add_parser("show", help="Render new entries (or full with --all)")
    show.add_argument("--all", action="store_true", help="Show full changelog")
    show.add_argument("--since", help="Show entries after YYYY-MM-DD")
    show.add_argument("--no-mark", action="store_true", help="Don't mark seen")

    ms = sub.add_parser("mark-seen", help="Mark entries seen up to a date")
    ms.add_argument("--through", help="Mark seen through YYYY-MM-DD (default: head)")

    args = parser.parse_args(argv)
    handlers = {
        "status": _cmd_status,
        "show": _cmd_show,
        "mark-seen": _cmd_mark_seen,
    }
    return handlers[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
