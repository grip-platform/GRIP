#!/usr/bin/env python3
"""NOTICE.md for the gift bundle: every shipped part, its source commit and the
licence it ships under, read from the part's own LICENSE file.

Plain English: a recipient opening the folder should see in one place what is in
it and under which terms, without opening four licence files. Each line is built
from what actually shipped: the part's folder in the bundle, the commit the files
were taken from, and the first non-empty line of that part's LICENSE as it is
written there. Nothing is inferred from a package name or a website; a part with
no LICENSE file is listed as shipping none, which is itself worth a reader's eye.

The one-folder product plan (item B1, 2026-10-18) records the hypothesis this rests
on: a verbatim licence listing suffices for the bundle's recipients. Its falsifier
is a licence whose terms need more than its heading (AGPL's source offer, for one),
in which case this file grows a per-part terms paragraph rather than a summary.
"""
from __future__ import annotations

from pathlib import Path

NOTICE_NAME = "NOTICE.md"
NO_LICENCE = "no LICENSE file shipped"


def licence_heading(part_dir: Path) -> str:
    """The first non-empty line of the part's LICENSE, as written; a sentence
    saying so when the part ships none."""
    for name in ("LICENSE", "LICENSE.md", "LICENSE.txt", "LICENCE"):
        path = part_dir / name
        if path.is_file():
            for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                if line.strip():
                    return line.strip()
            return f"{name} is empty"
    return NO_LICENCE


def build_notice(dest: Path, parts) -> Path:
    """Write NOTICE.md at the top of the bundle and return its path.

    `parts` is any sequence with `.name`, `.folder` and `.sha`; the order given is
    the order written, so the caller decides what a reader sees first.
    """
    lines = ["# NOTICE", "",
             "This folder ships the parts below. Each line gives the folder, the commit the",
             "files were taken from, and the licence heading exactly as that part's LICENSE",
             "file states it. The full terms are in each folder.", "",
             "| Part | Folder | Source commit | Licence (from its LICENSE file) |",
             "| --- | --- | --- | --- |"]
    for part in parts:
        heading = licence_heading(Path(dest) / part.folder)
        lines.append(f"| {part.name} | `{part.folder}/` | `{part.sha[:12]}` | {heading} |")
    path = Path(dest) / NOTICE_NAME
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path
