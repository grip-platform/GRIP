#!/usr/bin/env python3
"""
grip-forget — the inverse of /remember: free a memory record's index slot.

The measured premise (plans/add-c6-10-to-the-woolly-token.md, 2026-09-07): 470
memory records on disk, 118 with a pointer in the loaded MEMORY.md, 352 (75%)
with none — and the index itself 17% over its load limit. GRIP's problem is
not surplus memory; it is that eviction from the rival index happens by
POSITION in a truncated file, and the operator's own record
(memory/reference_memory_index_eviction_has_no_valid_key.md) warns that no
valid eviction key exists. /forget supplies the missing key: explicit operator
intent, one slug at a time, with a reason.

  --release <slug>   Remove the record's pointer line(s) from MEMORY.md, append
                     the exact removed line(s) to a `.forgotten` manifest beside
                     the index, and report what changed FROM A MEASUREMENT —
                     bin/grip-memory-lint's corpus_stats() before and after —
                     never from this module's own return value
                     (rules/verify-canonical-not-claimed.md).

The record stays on disk, byte-identical. Only its reachability changes, and
the manifest carries enough to reverse the release by hand (never-hard-delete).

Refuses, before any file is touched: a pinned record (the GRIP-PINNED block in
MEMORY.md is the unforgettable class — rules/binding-memory-records.md), a
slug with no pointer (nothing to release; never a silent success), a slug with
no record on disk (a dangling pointer is grip-memory-lint E006's job), and an
empty reason.

--retire (demotion down the knowledge-maturation functor) and --purge (genuine
erasure, lib/wa_opt_out.py's shape) are NOT declared here. --retire needs an
inverse arrow in rules/knowledge-maturation-functor.md that must be ratified
exogenously first; both ship in a second PR. A flag that acts on nothing is
forbidden (rules/declared-input-must-reach-the-actor.md), so the parser does
not accept them today.

Stdlib plus two in-tree modules: lib/remember.py (the memory-dir resolver, so
/forget and /remember can never disagree about which index they mean) and
lib/binding_memory.py (the pinned-block parser and the lint loader).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))
import binding_memory as bm  # noqa: E402
from claude_dir import get_claude_dir  # noqa: E402
# The resolver /remember uses, imported under its existing name on purpose: a
# session's sys.modules may already hold ``remember`` from the shared tree
# (rules/worktree-must-track-main.md), so a NEW public name would raise
# ImportError there while the checkout beside this file has it. Same for the
# lint loader below (``bm._lint_module``). Both names are stable in-tree helpers.
from remember import _resolve_memory_dir as resolve_memory_dir  # noqa: E402

MANIFEST_NAME = ".forgotten"


class ForgetRefused(ValueError):
    """A release that must not happen. Raised before any file is touched."""


@dataclass(frozen=True)
class ReleaseReport:
    """What one release changed — every count taken from corpus_stats()."""

    slug: str
    record: str
    index: str
    manifest: str
    removed_lines: list[str]
    pointer_lines_before: int
    pointer_lines_after: int
    orphans_before: int
    orphans_after: int

    @property
    def still_reachable(self) -> bool:
        """True when the orphan count did not move: the record is still reached
        through an inbound wikilink. False when this release left it reachable
        by grep only. Measured by the lint, not asserted by this module."""
        return self.orphans_after == self.orphans_before


def _pinned_span(lines: list[str]) -> range:
    """Line indices of the pinned block, or an empty range when there is none."""
    try:
        start = lines.index(bm.PINNED_START)
        end = lines.index(bm.PINNED_END, start + 1)
    except ValueError:
        return range(0)
    return range(start, end + 1)


def _pointer_indices(lines: list[str], slug: str, lint) -> list[int]:
    """Indices of MEMORY.md pointer lines whose target stem is ``slug``, using
    the lint's own POINTER_RE so the two readers cannot disagree about what a
    pointer is. Lines inside the pinned block are never candidates."""
    pinned = _pinned_span(lines)
    hits: list[int] = []
    for index, line in enumerate(lines):
        if index in pinned:
            continue
        match = lint.POINTER_RE.match(line)
        if match and match.group(1).endswith(".md") and Path(match.group(1)).stem == slug:
            hits.append(index)
    return hits


def _refuse_if_pinned(lines: list[str], slug: str) -> None:
    if any(entry.record == slug for entry in bm.block_entries(lines)):
        raise ForgetRefused(
            f"{slug} is pinned (GRIP-PINNED block, binding: irreversible) — the "
            "unforgettable class. Unpinning is a deliberate edit of the block, "
            "never a /forget."
        )


def _atomic_write(path: Path, text: str) -> None:
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(text)
    os.replace(tmp, path)


def _stats(root: Path, mem_dir: Path, lint) -> dict:
    return lint.corpus_stats(bm.build_corpus(root, mem_dir))


def _check_preconditions(mem_dir: Path, slug: str, reason: str) -> tuple[Path, Path]:
    if not str(reason or "").strip():
        raise ForgetRefused("a reason is required — the manifest records why a slot was freed")
    record = mem_dir / f"{slug}.md"
    index = mem_dir / "MEMORY.md"
    if not record.is_file():
        raise ForgetRefused(
            f"no record at {record} — a dangling pointer is grip-memory-lint E006's "
            "job, not a release"
        )
    if not index.is_file():
        raise ForgetRefused(f"no index at {index}")
    return record, index


def release(mem_dir: Path, slug: str, reason: str, *, root: Path | None = None) -> ReleaseReport:
    """Free ``slug``'s slot in ``mem_dir/MEMORY.md``; keep the record on disk.

    Order of refusals (each before any write): empty reason, missing record,
    missing index, pinned record, no pointer line. Then: measure, remove the
    pointer line(s) atomically, append the manifest line, measure again, and
    check the post-condition against the measurement — a release that did not
    shrink the pointer set by exactly one raises rather than reporting success.
    """
    root = Path(root) if root else get_claude_dir()
    mem_dir = Path(mem_dir)
    record, index = _check_preconditions(mem_dir, slug, reason)
    lint = bm._lint_module(root)  # see the import note above
    lines = index.read_text(encoding="utf-8").splitlines()
    _refuse_if_pinned(lines, slug)
    hits = _pointer_indices(lines, slug, lint)
    if not hits:
        raise ForgetRefused(
            f"no pointer to {slug} in {index} — nothing to release "
            "(to make the record reachable again, /remember it)"
        )
    before = _stats(root, mem_dir, lint)
    removed = [lines[i] for i in hits]
    kept = [line for i, line in enumerate(lines) if i not in set(hits)]
    _atomic_write(index, "\n".join(kept) + "\n")
    manifest = mem_dir / MANIFEST_NAME
    entry = {
        "slug": slug,
        "reason": reason.strip(),
        "released_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "removed_lines": removed,
        "by": "lib/forget.py --release",
    }
    with manifest.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    after = _stats(root, mem_dir, lint)
    if not record.is_file() or after["pointer_lines"] != before["pointer_lines"] - 1:
        raise RuntimeError(
            f"forget: post-condition failed for {slug}: pointer_lines "
            f"{before['pointer_lines']} -> {after['pointer_lines']}, record present="
            f"{record.is_file()} — the index was written; inspect {index} and {manifest}"
        )
    return ReleaseReport(
        slug=slug, record=str(record), index=str(index), manifest=str(manifest),
        removed_lines=removed,
        pointer_lines_before=before["pointer_lines"], pointer_lines_after=after["pointer_lines"],
        orphans_before=before["orphans"], orphans_after=after["orphans"],
    )


RAFIKI = (
    '\n  "The past can hurt. But you can either run from it, or learn from it." — Rafiki\n'
    "  The record is still on disk; only its slot in the loaded index is freed.\n"
)


def render(report: ReleaseReport, reason: str) -> str:
    reach = (
        "still reachable through an inbound wikilink"
        if report.still_reachable else "the record is now reachable by grep only"
    )
    return (
        f"RELEASED {report.slug} — {reason.strip()!r}\n"
        f"  index    : {report.index} ({len(report.removed_lines)} pointer line(s) removed)\n"
        f"  record   : {report.record} (still on disk, unchanged)\n"
        f"  manifest : {report.manifest}\n"
        f"  measured : pointer_lines {report.pointer_lines_before} -> "
        f"{report.pointer_lines_after}; orphans {report.orphans_before} -> "
        f"{report.orphans_after} — {reach}\n"
        f"{RAFIKI}"
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Free a memory record's index slot — the inverse of /remember."
    )
    ap.add_argument("--release", metavar="SLUG", required=True,
                    help="memory record stem whose MEMORY.md pointer line(s) to remove")
    ap.add_argument("--reason", required=True, help="why — recorded in the .forgotten manifest")
    ap.add_argument("--memory-dir", default=None, help="index directory (default: auto)")
    ap.add_argument("--root", default=None,
                    help="GRIP checkout holding bin/grip-memory-lint (default: CLAUDE_DIR)")
    a = ap.parse_args(argv)
    root = Path(a.root) if a.root else get_claude_dir()
    try:
        report = release(resolve_memory_dir(a.memory_dir), a.release, a.reason, root=root)
    except (ForgetRefused, bm.BindingError) as exc:
        print(f"forget: refused — {exc}", file=sys.stderr)
        return 2
    print(render(report, a.reason))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
