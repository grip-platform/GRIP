"""Detect the merged-branch magnet: new commits on a branch whose PR already merged.

THE FAILURE THIS CATCHES (observed twice on 2026-08-01, hours apart):

`security/remove-external-sync-docs` had its PR (#4154) merged on 2026-07-26. Work
kept landing on it afterwards anyway. The rsi-witness staleness watchdog was
committed there and sat unmerged for days while the script itself ran live on the
operator's Mac -- a guard whose source existed in no repository. Then at 01:38 on
2026-08-01, while that very incident was being cleaned up, a concurrent session
pushed *another* stray commit (`41fef209`) onto the same already-merged branch.

That is the shape: once a branch's PR merges, the branch stops being watched. Nobody
opens a second PR for it, CI results go unread, and anything committed there is
invisible until someone goes looking. It is a magnet for orphaned work.

WHY ANCESTRY CHECKS DO NOT WORK (verified on the real branch, 2026-08-01):

    git merge-base --is-ancestor origin/security/remove-external-sync-docs origin/main
    -> false

GRIP squash-merges. A squash rewrites the commits into one new commit with a fresh
SHA and a fresh patch-id, so the branch tip is NOT an ancestor of main and
`git cherry` finds no equivalent either. Both pure-git signals are structurally
blind here. This matches the existing operator memory
`feedback_r0_branch_prune_squash_merge_blindspot.md`, which reached the same
conclusion from the opposite direction (branch pruning).

So the only sound signal is the forge's own record: a PR whose `state` is MERGED and
whose `mergedAt` is set. That requires a `gh` call, which is why the lookup is
INJECTED here rather than performed inline -- it keeps this module pure, makes the
tests hermetic (no network), and lets a caller add its own caching or timeout.

FAIL-OPEN BY DESIGN. Every error path returns UNKNOWN, which does not warn. A guard
that blocks commits because GitHub was briefly unreachable would be worse than the
bug it prevents, and would itself become a reason to bypass the guard.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable, Mapping, Optional, Sequence

__all__ = [
    "Verdict",
    "OK",
    "MERGED_CLEAN",
    "MAGNET",
    "UNKNOWN",
    "PROTECTED_BRANCHES",
    "evaluate_branch",
    "has_merged_pr",
]

OK = "ok"
MERGED_CLEAN = "merged-clean"
MAGNET = "magnet"
UNKNOWN = "unknown"

#: Long-lived branches are merged into constantly; the pattern does not apply.
PROTECTED_BRANCHES = frozenset({"main", "master", "develop", "trunk", "HEAD"})

PrLookup = Callable[[str], Iterable[Mapping[str, object]]]


@dataclass(frozen=True)
class Verdict:
    """The guard's decision about one branch."""

    status: str
    branch: str
    reason: str
    merged_pr: Optional[int] = None

    @property
    def should_warn(self) -> bool:
        """True only when the branch demonstrably has a merged PR.

        UNKNOWN never warns -- see the fail-open note in the module docstring.
        """
        return self.status in (MERGED_CLEAN, MAGNET)


def _merged_prs(prs: Sequence[Mapping[str, object]]) -> list[Mapping[str, object]]:
    """Keep only PRs the forge actually recorded as merged.

    Both conditions are required. `state == "MERGED"` alone is not enough: a
    closed-then-reopened PR can carry a stale state in some payloads, whereas
    `mergedAt` is only ever set by a real merge.
    """
    out = []
    for pr in prs:
        if str(pr.get("state", "")).upper() != "MERGED":
            continue
        if not pr.get("mergedAt"):
            continue
        out.append(pr)
    return out


def has_merged_pr(prs: Iterable[Mapping[str, object]]) -> bool:
    """Does this lookup answer record a real merge, by the rule the guard decides on?

    Exported so the hook's cache counts "merged" exactly as `evaluate_branch` does.
    A merge is permanent, so an answer that records one may be reused. "Not merged
    yet" can change at any moment, and the commit just after a merge is the one this
    guard exists to catch (GRIP#6980), so such an answer must always be asked again.
    """
    return bool(_merged_prs(list(prs)))


def _latest(prs: Sequence[Mapping[str, object]]) -> Mapping[str, object]:
    """The most recently merged PR. ISO-8601 timestamps sort lexicographically."""
    return max(prs, key=lambda p: str(p.get("mergedAt", "")))


def _pr_number(pr: Mapping[str, object]) -> Optional[int]:
    try:
        return int(pr["number"])  # type: ignore[arg-type]
    except (KeyError, TypeError, ValueError):
        return None


def _pr_label(number: Optional[int]) -> str:
    """Human-readable PR reference that never renders a bare `None`.

    AGORA review of PR #4255 (quote-verified) caught the original interpolating a
    missing number straight into the message, producing "PR #None merged this
    branch". That reads like a cosmetic glitch while actually concealing a data
    problem: a PR record arrived without a usable number, which is worth saying
    out loud rather than papering over. The branch is still demonstrably merged --
    `mergedAt` was set -- so the warning must still fire; only its wording changes.
    """
    return f"PR #{number}" if number is not None else "a merged PR (number unreadable)"


def evaluate_branch(
    branch: str,
    head_sha: str,
    pr_lookup: PrLookup,
) -> Verdict:
    """Decide whether committing to `branch` is landing work on a merged branch.

    Args:
        branch: the branch about to receive a commit.
        head_sha: that branch's current tip SHA.
        pr_lookup: called with the branch name, returns PR records carrying
            `state`, `mergedAt`, `headRefOid` and `number`. Injected so this
            module stays pure and the tests need no network.

    Returns:
        MAGNET       -- a PR merged AND the tip has moved past it. Stray work is
                        already sitting here unnoticed; this is the real bug.
        MERGED_CLEAN -- a PR merged and the tip still matches it. Nothing is lost
                        yet, but the next commit would be the first stray one.
        OK           -- no merged PR, or a protected branch.
        UNKNOWN      -- lookup failed. Never warns.
    """
    if not branch or branch in PROTECTED_BRANCHES:
        return Verdict(OK, branch, "protected or empty branch name")

    try:
        prs = list(pr_lookup(branch))
    except Exception as exc:  # noqa: BLE001 - fail open on ANY lookup failure
        return Verdict(UNKNOWN, branch, f"PR lookup failed, failing open: {exc}")

    merged = _merged_prs(prs)
    if not merged:
        return Verdict(OK, branch, "no merged PR for this branch")

    pr = _latest(merged)
    number = _pr_number(pr)
    label = _pr_label(number)
    tip = str(pr.get("headRefOid") or "")

    if tip and head_sha and tip != head_sha:
        return Verdict(
            MAGNET,
            branch,
            (
                f"{label} merged this branch at {pr.get('mergedAt')}, but the tip "
                f"has moved since ({tip[:8]} -> {head_sha[:8]}). Commits made here now "
                f"sit unmerged and unwatched. Open a fresh branch off main instead."
            ),
            number,
        )

    return Verdict(
        MERGED_CLEAN,
        branch,
        (
            f"{label} already merged this branch at {pr.get('mergedAt')}. "
            f"Committing here would strand the work outside any open PR. "
            f"Open a fresh branch off main instead."
        ),
        number,
    )
