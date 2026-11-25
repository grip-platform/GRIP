"""The RSI criterion's review input: how often AGORA passed a merged pull
request at its first sitting (GRIP#9668).

The criterion used to read a Ralph reviewer's scores from
``state/rsi/rsi-convergence-state.json``. Nothing has written that file since the
Ralph workflow was removed (bb9b8f03d), so the input was always unmeasured and
the criterion could never be met. AGORA reviews every merged pull request, and
each sitting posts a comment carrying its verdict.

The first sitting is the one that grades the work as it was first offered. A
later sitting reviews the fix the first one asked for, so it is not counted. A
first sitting that said MERGE or DEFER CHANGES is a pass; any other outcome is
not, including the fail-closed reading ``parse_verdict`` gives a judge failure
and AGORA's FAILED banner for a sitting that could not produce a review, which
GRIP reads as no review, never as a pass (Codex on GRIP#9882).

A sitting counts only from the CI bot or from an owner, member or collaborator
of the repository, so another author's comment cannot forge one (AGORA on
GRIP#9882). A bot-only filter would not do: in the 7 days to 2026-11-25 all
261 first sittings came from a member's account (CI was paused, so AGORA ran
in sessions).

Pull requests closed without merging are left out. In the same 7 days 4 were
closed unmerged, 3 of them had a first sitting, and all 3 passed, so leaving
them out did not inflate the rate.

Measured on 2026-11-25 for the 7 days to that morning: 261 merged pull requests
had a first sitting, 96 MERGE, 153 DEFER CHANGES, 7 REQUEST CHANGES and 5 FAILED
banners, a pass rate of 95.4%. Fetching them took about 35 seconds, so the quick
``check`` reads what the newest full ``check`` recorded (``lib/auto_converge.py``).
"""
from __future__ import annotations

import json
import re
import subprocess
from datetime import date, timedelta
from pathlib import Path

try:  # package import, then script mode, as lib/ultrajudge.py does
    from lib.agora_verdict_router import _AGORA_HEADER, _BOT_LOGIN, parse_verdict
except ImportError:  # pragma: no cover - script mode
    from agora_verdict_router import _AGORA_HEADER, _BOT_LOGIN, parse_verdict

WINDOW_DAYS = 7
#: Fewer first sittings than this is too small a sample to let the loop declare
#: convergence on; it reads as unmeasured, never as a rate.
MIN_SITTINGS = 5
PASSING = frozenset({"MERGE", "DEFER_CHANGES"})
#: GitHub author associations whose AGORA comments count as sittings.
TRUSTED_ASSOCIATIONS = frozenset({"OWNER", "MEMBER", "COLLABORATOR"})
_GH_TIMEOUT_S = 180
_ORIGIN_RE = re.compile(r"github\.com[:/]([^/\s]+/[^/\s]+?)(?:\.git)?/?$")


def _trusted(comment: dict) -> bool:
    """True for an owner, member or collaborator of the repository, or for the
    CI bot. gh reported the bot as "github-actions" with association NONE on
    all 116 AGORA comments of the 50 PRs merged 2026-11-02 to 2026-11-08; REST
    spells it "github-actions[bot]", so the "[bot]" suffix is ignored."""
    if comment.get("authorAssociation") in TRUSTED_ASSOCIATIONS:
        return True
    login = (comment.get("author") or {}).get("login") or ""
    return login.removesuffix("[bot]") == _BOT_LOGIN.removesuffix("[bot]")


def _failed_banner(body: str) -> bool:
    """AGORA's banner for a sitting that could not produce a review: its first
    line is the review header with FAILED at the end (lib/agora_pr_review.py)."""
    first_line = body.split("\n", 1)[0].rstrip()
    return first_line.startswith(_AGORA_HEADER) and first_line.endswith("FAILED")


def first_verdict(comments: list) -> str | None:
    """The outcome of the earliest trusted AGORA sitting: its verdict, or
    "FAILED" for a FAILED banner. An untrusted author cannot forge a sitting
    (AGORA on GRIP#9882)."""
    for comment in sorted(comments, key=lambda c: c.get("createdAt") or ""):
        body = (comment.get("body") or "") if _trusted(comment) else ""
        outcome = parse_verdict(body) or ("FAILED" if _failed_banner(body) else None)
        if outcome:
            return outcome
    return None


def pass_rate(prs: list) -> tuple[float, int]:
    """(share of first sittings that passed, number of pull requests that had
    one). A pull request with no AGORA sitting at all is left out."""
    firsts = [v for v in (first_verdict(pr.get("comments") or []) for pr in prs) if v]
    if not firsts:
        return 0.0, 0
    return sum(v in PASSING for v in firsts) / len(firsts), len(firsts)


def origin_repo(repo_dir, runner=subprocess.run) -> str:
    """OWNER/REPO of the checkout's ``origin`` remote. Named explicitly to gh,
    because a bare ``gh pr list`` on the wrong repository returns an empty list
    with exit 0 (``rules/gh-repo-resolution.md``)."""
    res = runner(["git", "-C", str(repo_dir), "remote", "get-url", "origin"],
                 capture_output=True, text=True, timeout=30)
    match = _ORIGIN_RE.search((res.stdout or "").strip()) if res.returncode == 0 else None
    if not match:
        raise OSError(f"no GitHub origin remote in {repo_dir}")
    return match.group(1)


def merged_prs(repo: str, days: int = WINDOW_DAYS, runner=subprocess.run) -> list:
    """Pull requests merged in the last ``days`` days, with their comments."""
    since = (date.today() - timedelta(days=days)).isoformat()
    argv = ["gh", "pr", "list", "--repo", repo, "--state", "merged",
            "--search", f"merged:>={since}", "--limit", "1000", "--json", "number,comments"]
    res = runner(argv, capture_output=True, text=True, timeout=_GH_TIMEOUT_S)
    if res.returncode != 0:
        raise OSError(f"gh pr list exited {res.returncode}: {(res.stderr or '').strip()[:200]}")
    return json.loads(res.stdout or "[]")


def measure(repo_dir, days: int = WINDOW_DAYS, runner=subprocess.run) -> tuple:
    """(pass rate 0.0-1.0 or None, first sittings counted, reason when None)."""
    try:
        prs = merged_prs(origin_repo(repo_dir, runner), days, runner)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        return None, 0, f"could not list merged pull requests: {exc}"
    rate, sittings = pass_rate(prs)
    if sittings < MIN_SITTINGS:
        return None, sittings, (f"only {sittings} first AGORA sittings in the last "
                                f"{days} days (need {MIN_SITTINGS})")
    return rate, sittings, ""


if __name__ == "__main__":  # pragma: no cover - manual check
    rate, n, why = measure(Path(__file__).resolve().parent.parent)
    print(f"{rate:.1%} of {n} first sittings passed" if rate is not None else why)
