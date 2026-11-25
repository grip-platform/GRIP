"""Work digest gather shim for the /aurora command.

Collects a window of recent work from origin/main + GitHub PRs.
Reuses aurora_card's battle-tested gather helpers for commits/tests/
hypotheses — import, never duplicate (DRY).

Zero is not the same as unknown
-------------------------------
Every count here is ``int | None``: a number when the source answered, and
``None`` when it could not be asked. ``unmeasured`` names each such source
with a plain-English reason, so a renderer can say "could not read merged
PRs: gh exited 4" instead of quietly publishing "0 PRs merged".

Public API
----------
WorkDigest  — frozen dataclass; all fields default to safe empty values.
gather_digest(hours_back) -> WorkDigest  — impure, fail-soft per source.

CLI
---
  python3 -m lib.work_digest [--hours-back N]
  Prints the digest as JSON.
"""

import argparse
import dataclasses
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List

# Reuse aurora_card's fixed impure helpers (DRY — do not duplicate).
from lib.aurora_card import (
    _since_iso,
    _claude_dir,
    _run,
    _gather_commits,
    _gather_tests,
    _gather_hypotheses,
    safe_measure,
)

# ---------------------------------------------------------------------------
# Conventional-commit type extraction
# ---------------------------------------------------------------------------

# Ordered longest-first so prefix matching is unambiguous.
_CC_TYPES = (
    "refactor", "perf", "style", "chore", "docs",
    "test", "feat", "fix", "ci", "build", "revert",
)


def _cc_type(title: str) -> str:
    """Parse the conventional-commit type prefix from a PR title.

    Returns the type string (e.g. 'feat', 'fix') or 'other'.
    """
    lowered = title.lower().lstrip()
    for t in _CC_TYPES:
        if lowered.startswith(t + ":") or lowered.startswith(t + "("):
            return t
    return "other"


# ---------------------------------------------------------------------------
# Frozen dataclasses
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PREntry:
    """One merged (or open) PR in the digest."""
    number: int
    title: str
    url: str
    type: str  # conventional-commit type, e.g. 'feat', 'fix', 'other'


# Human labels for the digest's field names, used when reporting what could
# not be measured. Keyed by the attribute name so there is one source of truth.
FIELD_LABELS: dict = {
    "commit_count": "commits",
    "test_units": "test lines",
    "hypothesis_count": "hypotheses",
    "merged_prs": "merged PRs",
    "open_prs": "open PRs",
}


@dataclass(frozen=True)
class WorkDigest:
    """Snapshot of work in a time window.

    All integer fields default to 0; all list fields default to empty.
    The dataclass is frozen so it can be hashed and used as a dict key.

    A count is ``None`` when that source could not be read at all — which is a
    different fact from a count of 0, and must never be rendered as one. The
    PR lists cannot express that in themselves (an unread list and an empty
    list are both ``()``), so ``unmeasured`` is the authority for both: it
    carries one ``(field_name, reason)`` pair per source that failed.
    """
    hours_back: int = 24
    commit_count: int | None = 0
    test_units: int | None = 0
    hypothesis_count: int | None = 0
    # Merged PRs in the window — titles/numbers are VERBATIM (precision invariant).
    merged_prs: tuple = field(default_factory=tuple)  # tuple[PREntry, ...]
    # Currently-open PRs — for the "Live/Open" section.
    open_prs: tuple = field(default_factory=tuple)    # tuple[PREntry, ...]
    # Derived: type -> count from merged_prs.
    categories: tuple = field(default_factory=tuple)  # tuple[tuple[str,int], ...]
    # Sources that could not be read: ((field_name, plain-English reason), ...).
    unmeasured: tuple = field(default_factory=tuple)

    def unmeasured_reason(self, field_name: str) -> str | None:
        """Why ``field_name`` could not be read, or None if it was read fine."""
        for name, reason in self.unmeasured:
            if name == field_name:
                return reason
        return None

    @property
    def pr_count(self) -> int | None:
        """Merged-PR count, or None when the PR list could not be read.

        ``len(merged_prs)`` on its own is the bug this digest exists to avoid:
        an unread list is empty, and an empty list counts as zero.
        """
        if self.unmeasured_reason("merged_prs") is not None:
            return None
        return len(self.merged_prs)

    @property
    def open_pr_count(self) -> int | None:
        """Open-PR count, or None when the open-PR list could not be read."""
        if self.unmeasured_reason("open_prs") is not None:
            return None
        return len(self.open_prs)


def all_unmeasured(hours_back: int, reason: str) -> WorkDigest:
    """A digest in which NOTHING was measured — for a gather that blew up whole.

    Returning a default ``WorkDigest()`` there would report a night of zeros
    that nobody ever counted, which is the same lie one field at a time.
    """
    return WorkDigest(
        hours_back=hours_back,
        commit_count=None,
        test_units=None,
        hypothesis_count=None,
        unmeasured=tuple((name, reason) for name in FIELD_LABELS),
    )


def unmeasured_note(digest: WorkDigest) -> str:
    """One plain-English line naming every source that could not be read.

    Empty string when everything was measured, so a healthy render is unchanged.
    """
    if not digest.unmeasured:
        return ""
    parts = [
        f"{FIELD_LABELS.get(name, name)} ({reason})"
        for name, reason in digest.unmeasured
    ]
    return "could not measure: " + "; ".join(parts)


# ---------------------------------------------------------------------------
# PR list helpers (each independently fail-soft)
# ---------------------------------------------------------------------------

def _fetch_prs_merged(root: Path, since_date: str) -> tuple:
    """Fetch merged PRs on/after since_date. Returns (prs, reason)."""
    return _fetch_prs(
        [
            "gh", "pr", "list",
            "--state", "merged",
            "--search", f"merged:>={since_date}",
            "--json", "number,title,url",
            "--limit", "100",
        ],
        root,
    )


def _fetch_prs_open(root: Path) -> tuple:
    """Fetch currently-open PRs. Returns (prs, reason)."""
    return _fetch_prs(
        [
            "gh", "pr", "list",
            "--state", "open",
            "--json", "number,title,url",
            "--limit", "50",
        ],
        root,
    )


def _fetch_prs(cmd: List[str], root: Path) -> tuple:
    """Run one `gh pr list` and parse it. Returns ``(prs, reason)``.

    A non-empty ``reason`` means the list is UNKNOWN rather than empty: `gh`
    could not be asked, so no conclusion about PRs may be drawn from it.
    """
    res = _run(cmd, root)
    if not res.ok:
        return ((), res.reason)
    return _parse_pr_json(res.stdout)


def _parse_pr_json(raw: str) -> tuple:
    """Parse gh JSON output. Returns ``(prs, reason)``.

    A non-empty ``reason`` means the output could not be understood, so the
    empty PR tuple beside it means "we do not know", never "there were none".
    `gh pr list --json` prints ``[]`` for a genuine no-match, so a real zero
    always arrives as valid JSON and comes back with an empty reason.
    """
    if not raw:
        return ((), "gh produced no output")
    try:
        items = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return ((), "gh printed output that is not valid JSON")
    if not isinstance(items, list):
        return ((), "gh printed JSON that is not a list of PRs")
    return (tuple(_pr_entries(items)), "")


def _pr_entries(items: List) -> List[PREntry]:
    """Map parsed gh JSON items to PREntry; non-dict items are skipped."""
    result: List[PREntry] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "")
        result.append(
            PREntry(
                number=int(item.get("number") or 0),
                title=title,
                url=str(item.get("url") or ""),
                type=_cc_type(title),
            )
        )
    return result


def _build_categories(prs: List[PREntry]) -> tuple:
    """Return a sorted tuple of (type, count) pairs from the merged PR list."""
    counts: Dict[str, int] = {}
    for pr in prs:
        counts[pr.type] = counts.get(pr.type, 0) + 1
    return tuple(sorted(counts.items()))


# ---------------------------------------------------------------------------
# Main gather entry-point
# ---------------------------------------------------------------------------

def _safe_fetch_prs(label: str, fn) -> tuple:
    """Call a PR fetcher, turning a raised error into an honest unknown."""
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001 — a broken source must not crash the digest
        return ((), f"{label} could not be fetched: {str(exc)[:120]}")


def _count_reason(measured) -> str:
    """The failure reason for a count, or '' when it was genuinely measured."""
    return "" if measured.known else (measured.reason or "no reason reported")


def _unmeasured_pairs(**reason_by_field: str) -> tuple:
    """Keep only the fields that actually failed, as (name, reason) pairs."""
    return tuple((name, reason) for name, reason in reason_by_field.items() if reason)


def gather_digest(hours_back: int = 24) -> WorkDigest:
    """Collect a windowed WorkDigest. Fail-soft per source.

    "Fail-soft" here means a failed source is recorded as UNMEASURED, never as
    zero. The old version coalesced every failure with ``or 0`` / ``or []``,
    so a broken `gh` and a genuinely quiet night produced identical output.
    """
    root = _claude_dir()
    since = _since_iso(hours_back)

    # Refresh origin/main once so all git queries see landed work.
    _run(["git", "fetch", "origin", "main", "--quiet"], root)

    commits = safe_measure("commits", lambda: _gather_commits(root, since))
    tests = safe_measure("test lines", lambda: _gather_tests(root, since))
    hypotheses = safe_measure("hypotheses", lambda: _gather_hypotheses(root, since))
    merged_prs, merged_why = _safe_fetch_prs(
        "merged PRs", lambda: _fetch_prs_merged(root, since[:10])
    )
    open_prs, open_why = _safe_fetch_prs("open PRs", lambda: _fetch_prs_open(root))

    return WorkDigest(
        hours_back=hours_back,
        commit_count=commits.value,
        test_units=tests.value,
        hypothesis_count=hypotheses.value,
        merged_prs=tuple(merged_prs),
        open_prs=tuple(open_prs),
        categories=_build_categories(merged_prs),
        unmeasured=_unmeasured_pairs(
            commit_count=_count_reason(commits),
            test_units=_count_reason(tests),
            hypothesis_count=_count_reason(hypotheses),
            merged_prs=merged_why,
            open_prs=open_why,
        ),
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _digest_to_dict(digest: WorkDigest) -> dict:
    """Serialise WorkDigest to a JSON-safe dict."""
    d = dataclasses.asdict(digest)
    # Tuples of dataclasses come out as lists of dicts via asdict — correct.
    return d


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Gather a windowed work digest and print as JSON.",
    )
    parser.add_argument(
        "--hours-back",
        type=int,
        default=24,
        help="window size in hours (default: 24)",
    )
    args = parser.parse_args()
    digest = gather_digest(args.hours_back)
    print(json.dumps(_digest_to_dict(digest), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
