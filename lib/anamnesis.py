#!/usr/bin/env python3
"""anamnesis — surface the most-forgotten memory entry.

Plato's ἀνάμνησις: the soul recalling what it once knew. Every existing GRIP
retrieval tool surfaces what's HOT (recent commits, active hypotheses, fresh
insights, KONO similarity). This is the dual — what's been QUIET for too
long, ranked by days since touched. Use when you suspect GRIP is acting
from a stale mental model — the entry you'd never have queried by name.

Falsifiable. Wrong if the most-forgotten entry is consistently noise that
should be deleted rather than recalled — at which point the right move is
pruning the index, not running this tool.
"""
from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path

from claude_dir import get_claude_dir
from path_encoding import encode_project_path

_CLAUDE_DIR = get_claude_dir()
MEMORY_ROOT = _CLAUDE_DIR / "projects" / encode_project_path(_CLAUDE_DIR) / "memory"
INDEX = MEMORY_ROOT / "MEMORY.md"

LINK_RE = re.compile(r"-\s+\[([^\]]+)\]\(([^)]+\.md)\)")

CATEGORY_HEADER_RE = re.compile(r"^##\s+(.+?)\s*$")


def parse_index() -> list[tuple[str, str, Path]]:
    """Return (category, title, path) for every memory link in MEMORY.md."""
    if not INDEX.exists():
        sys.exit(f"MEMORY.md not found at {INDEX}")
    entries: list[tuple[str, str, Path]] = []
    category = "uncategorised"
    for line in INDEX.read_text().splitlines():
        header = CATEGORY_HEADER_RE.match(line)
        if header:
            category = header.group(1)
            continue
        match = LINK_RE.search(line)
        if not match:
            continue
        title, target = match.groups()
        if target.startswith("./"):
            target = target[2:]
        path = MEMORY_ROOT / target
        if path.exists():
            entries.append((category, title, path))
    return entries


def silence_days(path: Path) -> float:
    return (time.time() - path.stat().st_mtime) / 86400.0


def render_entry(category: str, title: str, path: Path, body_lines: int) -> str:
    days = silence_days(path)
    body = path.read_text().strip()
    out: list[str] = [
        f"  category: {category}",
        f"  title:    {title}",
        f"  silent:   {days:.1f} days",
        f"  source:   {path.name}",
        "",
    ]
    for line in body.splitlines()[:body_lines]:
        out.append(f"    {line}")
    return "\n".join(out)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="anamnesis",
        description="Surface the most-forgotten memory entries (Plato's recollection).",
    )
    parser.add_argument(
        "-n", "--top", type=int, default=1,
        help="Number of forgotten entries to surface (default 1).",
    )
    parser.add_argument(
        "-b", "--body-lines", type=int, default=20,
        help="Body lines to display per entry (default 20).",
    )
    parser.add_argument(
        "--min-days", type=float, default=0.0,
        help="Only surface entries quiet for at least this many days.",
    )
    parser.add_argument(
        "--category", default=None,
        help="Restrict to one MEMORY.md category (substring match).",
    )
    args = parser.parse_args(argv[1:])

    entries = parse_index()
    if args.category:
        needle = args.category.lower()
        entries = [e for e in entries if needle in e[0].lower()]
    if not entries:
        sys.exit("No memory entries found matching filters.")

    ranked = sorted(entries, key=lambda e: silence_days(e[2]), reverse=True)
    surfaced = [e for e in ranked if silence_days(e[2]) >= args.min_days][: args.top]
    if not surfaced:
        sys.exit(f"No entries quiet for at least {args.min_days} days.")

    print("anamnesis (anamnesis) - the most-forgotten memory entries:\n")
    for category, title, path in surfaced:
        print(render_entry(category, title, path, args.body_lines))
        print()

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
