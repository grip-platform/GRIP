#!/usr/bin/env python3
"""Session cleanup — runs at session start to prune cc-releases and expire cache.

Designed for speed: only mtime checks, no file content reads.
Never crashes the session — all errors are caught and swallowed.
Output: single-line summary to stdout.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", Path.home() / ".claude"))
CC_RELEASES_DIR = CLAUDE_DIR / "data" / "cc-releases"
CC_RELEASES_KEEP = 10
CC_RELEASES_ARCHIVE = CC_RELEASES_DIR / "archive"

try:  # one shared source of truth, so the two lists cannot drift apart
    from cc_feature_scanner import NON_RELEASE_FILES as CC_RELEASES_SKIP
except Exception:  # cleanup must never crash the session
    CC_RELEASES_SKIP = frozenset({
        "harness-backlog.md", "harness-state.json", "validation-adjustments.json",
    })


def _version_key(name: str) -> tuple[int, ...] | None:
    """Parse 'v2.1.226.json' into (2, 1, 226); None if it is not a release file.

    Anything that does not parse is left alone rather than archived, so a new
    bookkeeping file dropped into this directory is never mistaken for an old
    release and moved out from under whatever writes it.
    """
    stem = name[1:-5] if name.startswith("v") and name.endswith(".json") else ""
    parts = stem.split(".")
    if not stem or not all(p.isdigit() for p in parts):
        return None
    return tuple(int(p) for p in parts)


def _tracked_names(directory: Path) -> frozenset | None:
    """Basenames git tracks directly under ``directory``.

    An empty set when the directory is not inside a checkout — nothing can be
    tracked there, so pruning is as safe as it ever was. None when git could
    not answer at all, which the caller reads as "nothing is proven untracked"
    and prunes nothing: the cost of a few extra JSON files is nothing, the
    cost of moving a committed one is below.
    """
    # 10 s is an upper bound on reading a LOCAL index (no network is involved;
    # the real call takes milliseconds). It exists so a wedged git can never
    # hold up a session start: on expiry TimeoutExpired, a SubprocessError, takes
    # the None path below and the pruner moves nothing.
    try:
        out = subprocess.run(
            ["git", "-C", str(directory), "ls-files", "-z"],
            capture_output=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return frozenset()
    names = out.stdout.decode("utf-8", "surrogateescape").split("\0")
    return frozenset(n for n in names if n and "/" not in n)


def _prune_cc_releases() -> int:
    """Keep the newest CC_RELEASES_KEEP releases by version; archive the rest —
    but NEVER a release git tracks.

    Ordering is by parsed version, never by mtime. A bulk re-fetch rewrites
    every file in the same second, which made an mtime sort keep an arbitrary
    handful and archive the newest releases alongside the oldest.

    A tracked file is never a candidate, whatever its version. The auto-harness
    fetches new releases into this directory untracked, so once ten of those
    exist every COMMITTED release is below the keep window. This pruner then
    moved them to the gitignored archive at every SessionStart, and
    ``git status`` on the shared tree showed 22 deleted ``data/cc-releases/*.json``
    that reappeared after every operator reset. Measured 2026-10-01. Moving a
    tracked file is not cleanup; it is an uncommitted deletion.

    Archiving does not lose history: cc_feature_scanner reads the archive too.

    Returns the number of files archived.
    """
    if not CC_RELEASES_DIR.is_dir():
        return 0
    tracked = _tracked_names(CC_RELEASES_DIR)
    if tracked is None:
        return 0

    keyed = []
    for f in CC_RELEASES_DIR.iterdir():
        if not f.is_file() or f.name in CC_RELEASES_SKIP:
            continue
        key = _version_key(f.name)
        if key is not None:
            keyed.append((key, f))
    keyed.sort(key=lambda pair: pair[0])
    version_files = [f for _, f in keyed]

    below_window = version_files[:-CC_RELEASES_KEEP] if len(version_files) > CC_RELEASES_KEEP else []
    to_archive = [f for f in below_window if f.name not in tracked]
    if not to_archive:
        return 0

    CC_RELEASES_ARCHIVE.mkdir(parents=True, exist_ok=True)
    count = 0
    for f in to_archive:
        dest = CC_RELEASES_ARCHIVE / f.name
        shutil.move(str(f), str(dest))
        count += 1
    return count


def _cleanup_cache() -> int:
    """Call StateManager.cleanup_expired_cache() and return archived file count."""
    try:
        sys.path.insert(0, str(CLAUDE_DIR))
        from lib.state_manager import StateManager  # noqa: PLC0415
        sm = StateManager()
        report = sm.cleanup_expired_cache()
        return sum(1 for action in report.values() if action.startswith("archived"))
    except Exception:
        return 0


def main() -> None:
    try:
        cache_archived = _cleanup_cache()
    except Exception:
        cache_archived = 0

    try:
        releases_pruned = _prune_cc_releases()
    except Exception:
        releases_pruned = 0

    print(f"[cleanup] {cache_archived} cache files archived, {releases_pruned} cc-releases pruned")


if __name__ == "__main__":
    main()
