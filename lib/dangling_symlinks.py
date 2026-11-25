#!/usr/bin/env python3
"""Detect dangling (broken-target) *tracked* symlinks — the class that breaks
cross-repo GitHub Actions staging for any consumer of a composite action.

Origin: GRIP #3881 removed 4 dangling symlinks (`skills/_archived/*` +
`.claude/skills/vanish`) that broke `grip-ci-substrate` staging in adopter repos
(HAL, nexus-poc). AGORA's DEFER on that PR asked for a proactive guard. This is
it — with two consumers: the pytest forward-anchor
(`tests/test_dangling_symlinks.py`, the CI gate) and the `__main__` CLI (a
pre-publish / pre-commit sweep).

`find -xtype` is GNU-only (absent on macOS BSD find and Windows Git Bash), so
this enumerates via `git ls-files` + stdlib `os.path`. A tracked symlink is
DANGLING iff `islink and not exists`. Only TRACKED paths are checked: a
consumer's checkout only stages what git tracks.

Scope — SUPERPROJECT only. Submodule *contents* are intentionally out of scope:
they belong to the submodule's own tree, `actions/checkout` does not fetch them
unless `submodules: true`, and each submodule should carry its own guard.
`--recurse-submodules` is deliberately NOT used — it would make this repo's CI
fail on a submodule's dangling symlink that this repo cannot fix.

Platform — validated on Linux + macOS; the enforcement point is Linux CI, which
is authoritative. On Windows without `core.symlinks=true`, git checks a symlink
out as a plain text file, so `is_symlink()` is False and it is not flagged; a
Windows-local CLI run may therefore under-report (the Linux CI gate still holds).
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_GIT_TIMEOUT_S = 30


def _tracked(repo_root: Path) -> list[str]:
    """Repo-relative paths git tracks in the superproject. No mutation."""
    out = subprocess.run(
        ["git", "-C", str(repo_root), "ls-files"],
        capture_output=True,
        text=True,
        check=True,
        timeout=_GIT_TIMEOUT_S,
    )
    return [rel for rel in out.stdout.splitlines() if rel.strip()]


def find_dangling(repo_root: Path, tracked: list[str] | None = None) -> list[str]:
    """Sorted repo-relative paths of tracked symlinks with an unresolvable target.

    Pass a pre-fetched `_tracked()` list as `tracked` to avoid a redundant
    `git ls-files`. Deterministic (sorted). Reads the filesystem; never writes.
    """
    if tracked is None:
        tracked = _tracked(repo_root)
    return sorted(
        rel
        for rel in tracked
        # islink: it IS a symlink; not exists: its target does not resolve.
        if (repo_root / rel).is_symlink() and not (repo_root / rel).exists()
    )


def _git_toplevel(start: Path) -> Path:
    out = subprocess.run(
        ["git", "-C", str(start), "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        check=True,
        timeout=_GIT_TIMEOUT_S,
    )
    return Path(out.stdout.strip())


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    start = Path(argv[0]) if argv else Path.cwd()
    repo = _git_toplevel(start)
    tracked = _tracked(repo)  # enumerate once; reused by find_dangling + the count
    dangling = find_dangling(repo, tracked=tracked)
    if dangling:
        print(
            "DANGLING SYMLINK(S) — these break cross-repo GitHub Actions staging "
            f"(origin: GRIP #3881). {len(dangling)} found:",
            file=sys.stderr,
        )
        for rel in dangling:
            print(f"  {rel}", file=sys.stderr)
        return 1
    print(f"OK: no dangling tracked symlinks ({len(tracked)} tracked paths scanned)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
