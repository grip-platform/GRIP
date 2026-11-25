"""Report whether the shared GRIP tree is serving canonical code.

``~/.claude`` is a symlink to the shared working tree, and launchd agents execute
code from it (observed 2026-08-03: ``com.grip.channel``, ``com.grip.cos``,
``com.grip.heartbeat``, ``com.grip.mesh-bus``, ``com.grip.mesh-client``). Whatever
branch the last interactive session happened to leave checked out is therefore both
what production runs AND what any verification read returns.

On 2026-08-03 that tree had been parked on a feature branch for eight days -- its
reflog shows ``main -> security/remove-external-sync-docs`` on 07-26, then
``-> docs/canonical-output-style-plain-fluent`` on 08-01. Three independent reads in
a single session silently came back wrong because of it:

* a forensics agent cited line numbers from pre-fix code,
* ``git log`` reported 36 commits for a window where ``origin/main`` had 72,
* ``lib/pdf_branding.py`` was missing a function that had already merged.

None of those announced themselves, because the tree is a perfectly valid checkout
answering correct questions about the wrong ref. It reads as healthy, which is
exactly why it is dangerous -- the same class as every other silent failure fixed
during that week.

This module is the deterministic check. It answers one question -- *is the shared
tree serving canonical code?* -- and, when git cannot answer, says so rather than
returning a reassuring default. A check that cannot fail is not a check
(``rules/tests-must-be-able-to-fail.md``); a check that reports OK when it does not
know is worse, because it manufactures the very confidence it exists to withhold.

Pure and stdlib-only apart from invoking ``git``; no writes, no network.
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

CANONICAL_REF = "origin/main"

#: Verdicts, ordered worst-to-best for reporting.
OFF_CANONICAL = "OFF_CANONICAL"
BEHIND = "BEHIND"
INDETERMINATE = "INDETERMINATE"
OK = "OK"

#: Exit codes for the CLI. ``INDETERMINATE`` is deliberately NOT 0 -- "git did not
#: run" must never be indistinguishable from "the tree is fine".
EXIT_CODES = {OK: 0, OFF_CANONICAL: 1, BEHIND: 2, INDETERMINATE: 3}


@dataclass(frozen=True)
class TreeState:
    """What the shared tree is currently serving.

    ``behind`` is the count of canonical commits absent from HEAD. It is ``None``
    whenever that number could not be computed -- an unknown distance is reported as
    unknown, never as zero.
    """

    branch: Optional[str]
    head: Optional[str]
    canonical_head: Optional[str]
    behind: Optional[int]
    verdict: str
    detail: str

    @property
    def is_canonical(self) -> bool:
        return self.verdict == OK

    @property
    def exit_code(self) -> int:
        return EXIT_CODES[self.verdict]


def _git(tree: Path, *args: str, timeout: int = 10) -> Optional[str]:
    """Run git in ``tree``; return stdout, or None if git could not answer.

    Every failure mode collapses to None on purpose. The caller turns that into
    INDETERMINATE, so a missing binary, a non-repo path and a timeout are all
    reported as "unknown" rather than silently becoming a passing check.
    """
    try:
        proc = subprocess.run(
            ["git", "-C", str(tree), *args],
            capture_output=True, text=True, timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.strip()


def _behind_count(tree: Path, canonical: str) -> Optional[int]:
    """Canonical commits not reachable from HEAD, or None if uncountable."""
    out = _git(tree, "rev-list", "--count", f"HEAD..{canonical}")
    if out is None or not out.isdigit():
        return None
    return int(out)


def _describe(branch: str, behind: Optional[int], canonical: str) -> str:
    if behind is None:
        return f"on {branch}; distance from {canonical} could not be counted"
    return f"on {branch}; {behind} commit(s) behind {canonical}"


def tree_state(tree: Path, canonical: str = CANONICAL_REF) -> TreeState:
    """Classify what ``tree`` is serving relative to ``canonical``.

    The branch NAME is the primary signal rather than the commit distance, because
    a feature branch that happens to sit at the same commit as main today will
    diverge the moment either moves -- and the danger is the standing arrangement,
    not one lucky instant.
    """
    branch = _git(tree, "rev-parse", "--abbrev-ref", "HEAD")
    head = _git(tree, "rev-parse", "--short", "HEAD")
    if branch is None or head is None:
        return TreeState(
            branch=branch, head=head, canonical_head=None, behind=None,
            verdict=INDETERMINATE,
            detail=f"git could not report the state of {tree}",
        )

    canonical_head = _git(tree, "rev-parse", "--short", canonical)
    behind = _behind_count(tree, canonical)

    if branch != "main":
        return TreeState(
            branch=branch, head=head, canonical_head=canonical_head, behind=behind,
            verdict=OFF_CANONICAL,
            detail=(
                f"serving branch '{branch}', not main -- code read from or executed "
                f"in this tree is whatever that branch contains"
            ),
        )
    if canonical_head is None or behind is None:
        return TreeState(
            branch=branch, head=head, canonical_head=canonical_head, behind=behind,
            verdict=INDETERMINATE,
            detail=f"on main, but {canonical} was unreachable -- run git fetch",
        )
    if behind > 0:
        return TreeState(
            branch=branch, head=head, canonical_head=canonical_head, behind=behind,
            verdict=BEHIND, detail=_describe(branch, behind, canonical),
        )
    return TreeState(
        branch=branch, head=head, canonical_head=canonical_head, behind=0,
        verdict=OK, detail=_describe(branch, 0, canonical),
    )
