#!/usr/bin/env python3
"""Parse a TWR "Copy as context" tower from the macOS clipboard (or stdin).

TWR (CodeTonight-SA/twr) "Copy as context" writes ONE markdown block to the
system clipboard. Its producer is electron/main/core/context-formatter.ts
``formatTowerAsContext``, which emits:

    # Context: <tower>

    _<N> items from "<tower>", oldest first._

    <clip 1>

    ---

    <clip 2>

Clips that are confidently code are wrapped in a ``` fence with a detected
language. An empty tower emits ``# Context: <name>\\n\\n_(empty)_``.

This module recovers ``(tower, clips[])`` so a GRIP session can ingest the
tower as working context. The integration surface is the system clipboard —
TWR needs no change.

Pure-stdlib. ``parse_twr_context`` is side-effect-free; the CLI reads pbpaste
or stdin and prints a summary or JSON.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys

_HEADER_RE = re.compile(r"# Context: (.+)")
_PROVENANCE_RE = re.compile(r'^_(\d+) items? from ".*", oldest first\._$')
_CLIP_SEP = "\n\n---\n\n"
_EMPTY_MARKER = "_(empty)_"


def parse_twr_context(text):
    """Parse a TWR copy-as-context payload. Return ``None`` if not one.

    Returns ``{"tower": str, "count": int, "clips": list[str],
    "declared_count": int | None}``. ``count`` is the true number of parsed
    clips; ``declared_count`` is the N stated in the provenance line (``None``
    when absent), so a caller can flag a producer/parser mismatch.
    """
    if not text:
        return None
    stripped = text.strip()
    header = _HEADER_RE.match(stripped)
    if not header:
        return None
    tower = header.group(1).strip()
    rest = stripped[header.end():].strip("\n").strip()

    if rest in ("", _EMPTY_MARKER):
        declared = 0 if rest == _EMPTY_MARKER else None
        return {"tower": tower, "count": 0, "clips": [], "declared_count": declared}

    first_nl = rest.find("\n")
    if first_nl == -1:
        prov = _PROVENANCE_RE.match(rest)
        if prov:  # header + provenance, no body
            return {"tower": tower, "count": 0, "clips": [], "declared_count": int(prov.group(1))}
        return {"tower": tower, "count": 1, "clips": [rest], "declared_count": None}

    prov = _PROVENANCE_RE.match(rest[:first_nl])
    if prov:
        declared = int(prov.group(1))
        body = rest[first_nl + 1:].strip("\n")
    else:  # no provenance line — the whole remainder is clip body
        declared = None
        body = rest
    clips = [c.strip() for c in body.split(_CLIP_SEP)] if body.strip() else []
    return {"tower": tower, "count": len(clips), "clips": clips, "declared_count": declared}


def _read_source(source):
    """Read the payload from stdin (``-``) or the macOS clipboard (pbpaste)."""
    if source == "-":
        return sys.stdin.read()
    try:
        result = subprocess.run(["pbpaste"], capture_output=True, text=True, timeout=5)
    except FileNotFoundError:
        sys.stderr.write(
            "pbpaste not found — the clipboard path is macOS-only; "
            "pass `--source -` to read a payload from stdin.\n"
        )
        raise SystemExit(2)
    return result.stdout


def _print_summary(parsed):
    tower = parsed["tower"]
    clips = parsed["clips"]
    print(f'TWR tower "{tower}" — {len(clips)} clip(s):')
    declared = parsed.get("declared_count")
    if declared not in (None, len(clips)):
        print(f"  (note: provenance declared {declared}, parsed {len(clips)})")
    for i, clip in enumerate(clips, 1):
        preview = clip if len(clip) <= 200 else clip[:200] + "…"
        print(f"\n[{i}]\n{preview}")


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Parse a TWR 'Copy as context' tower from the clipboard."
    )
    parser.add_argument(
        "--source", default="pbpaste",
        help="'pbpaste' (default, macOS clipboard) or '-' for stdin",
    )
    parser.add_argument("--json", action="store_true", help="emit JSON instead of a summary")
    args = parser.parse_args(argv)

    text = _read_source("-" if args.source == "-" else "pbpaste")
    parsed = parse_twr_context(text)
    if parsed is None:
        sys.stderr.write(
            "No TWR context on the clipboard (expected a block starting with "
            "'# Context: ').\nIn TWR: select a tower → Copy as context, then re-run.\n"
        )
        return 1
    if args.json:
        print(json.dumps(parsed, ensure_ascii=False, indent=2))
    else:
        _print_summary(parsed)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
