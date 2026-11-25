"""Work summary shim for the /aurora command.

Transforms a WorkDigest into a WorkSummary with three tiers:
  tldr  — one deterministic headline string.
  plain — categorised PR listing, evidence tokens VERBATIM.
  eli5  — optional HAL-enriched narrative (fail-soft to None).
  verify— tuple of (pr_number, bool) via gh state check.

Public API
----------
WorkSummary — frozen dataclass.
summarise(digest, eli5) -> WorkSummary

CLI
---
  python3 -m lib.work_summary [--hours-back N] [--eli5]
  Gathers then summarises; prints JSON.

Design notes
------------
- tldr/plain are 100% deterministic (no LLM).
- eli5 fires ONLY when requested; always fail-soft to None.
- Evidence tokens (PR numbers, titles) are NEVER rounded or altered.
- verify calls gh pr view per merged PR — each call is independently fail-soft.
- No nested for-loops in one function (BIG-O gate compliance); loops extracted.
"""

import argparse
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from lib.aurora_card import format_count
from lib.work_digest import WorkDigest, PREntry, gather_digest


# ---------------------------------------------------------------------------
# Frozen result dataclass
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class WorkSummary:
    """Summarised view of a WorkDigest.

    Attributes
    ----------
    tldr:   One-line headline.  Always present.
    plain:  Multi-line categorised text.  Always present.
    eli5:   Short prose narrative from HAL.  None when not requested or failed.
    verify: Tuple of (pr_number, is_merged) pairs.
    """
    tldr: str
    plain: str
    eli5: Optional[str]
    verify: Tuple[Tuple[int, bool], ...]


# ---------------------------------------------------------------------------
# tldr builder (deterministic)
# ---------------------------------------------------------------------------

def _build_tldr(digest: WorkDigest) -> str:
    """One-line deterministic headline from the digest.

    This string is the Slack post's own text, so it is the sentence a teammate
    actually reads. When the PR list could not be read it must SAY so: "0 PRs
    in 24h" is indistinguishable from a quiet night and is read as fact.
    """
    h = digest.hours_back
    unread = digest.unmeasured_reason("merged_prs")
    if unread is not None:
        return f"could not read merged PRs for the last {h}h — {unread}"
    n = len(digest.merged_prs)
    cat_parts = [f"{count} {t}" for t, count in digest.categories if count > 0]
    cats = ", ".join(cat_parts) if cat_parts else "no PRs"
    pr_word = "PR" if n == 1 else "PRs"
    return f"{n} {pr_word} in {h}h — {cats}"


# ---------------------------------------------------------------------------
# plain builder helpers (each single-loop, no nesting)
# ---------------------------------------------------------------------------

def _format_pr_line(pr: PREntry) -> str:
    """Format one PR entry — title and number are VERBATIM."""
    return f"  • {pr.title} (#{pr.number})"


def _group_prs_by_type(prs: Tuple[PREntry, ...]) -> Dict[str, List[PREntry]]:
    """Group PREntry objects by their type field."""
    groups: Dict[str, List[PREntry]] = {}
    for pr in prs:
        groups.setdefault(pr.type, []).append(pr)
    return groups


def _render_category_block(t: str, count: int, groups: Dict[str, List[PREntry]]) -> List[str]:
    """Render lines for one category block."""
    group = groups.get(t, [])
    if not group:
        return []
    lines = [f"\n[{t}] ({count})"]
    for pr in group:
        lines.append(_format_pr_line(pr))
    return lines


def _render_open_prs(digest: WorkDigest) -> List[str]:
    """Render the open/in-flight section, or say the list could not be read."""
    unread = digest.unmeasured_reason("open_prs")
    if unread is not None:
        return [f"\nOpen / in-flight: could not be read — {unread}"]
    if not digest.open_prs:
        return []
    lines = [f"\nOpen / in-flight ({len(digest.open_prs)}):"]
    for pr in digest.open_prs:
        lines.append(_format_pr_line(pr))
    return lines


def _render_merged_section(digest: WorkDigest) -> List[str]:
    """The merged-PR section: the listing, a real "none", or "could not read"."""
    unread = digest.unmeasured_reason("merged_prs")
    if unread is not None:
        return [
            f"Merged PRs for the last {digest.hours_back}h could not be read "
            f"— {unread}. This is NOT the same as none being merged."
        ]
    if not digest.merged_prs:
        return [f"No PRs merged in the last {digest.hours_back}h."]
    lines = [f"Merged in the last {digest.hours_back}h:"]
    groups = _group_prs_by_type(digest.merged_prs)
    for t, count in digest.categories:
        lines.extend(_render_category_block(t, count, groups))
    return lines


def _counts_line(digest: WorkDigest) -> str:
    """The commits/tests/hypotheses tally, each honest about being unmeasured."""

    def _one(field_name: str, value) -> str:
        return format_count(value, digest.unmeasured_reason(field_name) or "")

    return (
        f"Commits on origin/main: {_one('commit_count', digest.commit_count)} | "
        f"Test lines: {_one('test_units', digest.test_units)} | "
        f"Hypotheses: {_one('hypothesis_count', digest.hypothesis_count)}"
    )


def _build_plain(digest: WorkDigest) -> str:
    """Build the categorised plain text from the digest."""
    lines: List[str] = list(_render_merged_section(digest))
    lines.append("")
    lines.append(_counts_line(digest))
    lines.extend(_render_open_prs(digest))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# eli5 builder (optional, fail-soft)
# ---------------------------------------------------------------------------

def _build_eli5(digest: WorkDigest, plain: str) -> Optional[str]:
    """Ask HAL for a short plain-English narrative. Returns None on any failure."""
    try:
        from lib.hal_llm_adapter import LlamaEngine  # type: ignore
    except ImportError:
        return None

    prompt = (
        "Write 2-3 short sentences explaining what the team shipped recently, "
        "in plain English for someone who doesn't write code. "
        "Do NOT include PR numbers, titles, or any technical tokens — those are "
        "shown separately. Focus on the human impact of the work.\n\n"
        f"Work summary (do not repeat these verbatim):\n{plain}"
    )
    try:
        engine = LlamaEngine()
        result = engine.chat(
            messages=[{"role": "user", "content": prompt}],
            max_tokens=200,
        )
        if result and result.strip():
            return result.strip()
        return None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# verify builder (per-PR gh probe, fail-soft)
# ---------------------------------------------------------------------------

def _check_pr_merged(number: int, root: Path) -> Optional[bool]:
    """Return True iff gh reports state==MERGED for this PR. None on failure."""
    try:
        out = subprocess.run(
            ["gh", "pr", "view", str(number), "--json", "state"],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        data = json.loads(out.stdout.strip())
        return data.get("state") == "MERGED"
    except Exception:
        return None


def _build_verify(
    merged_prs: Tuple[PREntry, ...],
    root: Path,
) -> Tuple[Tuple[int, bool], ...]:
    """Verify-canonical each merged PR via gh. Missing -> omitted."""
    results: List[Tuple[int, bool]] = []
    for pr in merged_prs:
        verdict = _check_pr_merged(pr.number, root)
        if verdict is not None:
            results.append((pr.number, verdict))
    return tuple(results)


# ---------------------------------------------------------------------------
# Main summarise entry-point
# ---------------------------------------------------------------------------

def summarise(digest: WorkDigest, eli5: bool = False) -> WorkSummary:
    """Produce a WorkSummary from a WorkDigest.

    Args:
        digest: the gathered data.
        eli5:   when True, attempt a HAL-enriched narrative (fail-soft to None).

    Returns:
        WorkSummary with tldr, plain, optional eli5, and verify tuple.
    """
    from lib.aurora_card import _claude_dir

    tldr = _build_tldr(digest)
    plain = _build_plain(digest)
    narrative = _build_eli5(digest, plain) if eli5 else None
    verify = _build_verify(digest.merged_prs, _claude_dir())

    return WorkSummary(
        tldr=tldr,
        plain=plain,
        eli5=narrative,
        verify=verify,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Gather and summarise recent work; print as JSON.",
    )
    parser.add_argument(
        "--hours-back",
        type=int,
        default=24,
        help="window size in hours (default: 24)",
    )
    parser.add_argument(
        "--eli5",
        action="store_true",
        help="attempt HAL-enriched narrative (fail-soft to null)",
    )
    args = parser.parse_args()
    digest = gather_digest(args.hours_back)
    summary = summarise(digest, eli5=args.eli5)
    out = {
        "tldr": summary.tldr,
        "plain": summary.plain,
        "eli5": summary.eli5,
        "verify": {str(n): v for n, v in summary.verify},
    }
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
