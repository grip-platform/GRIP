"""
CC PR Status — query and expose PR review state via gh CLI.

Provides get_pr_review_status() for the /create-pr workflow and
load_pr_context() for --from-pr session resume.

Closes #1152 (review status integration).
Part of #1153 (--from-pr session resume).
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class PRReviewStatus:
    """Review state of a pull request."""

    pr_number: int
    state: str  # APPROVED, CHANGES_REQUESTED, PENDING, REVIEW_REQUIRED, COMMENTED
    title: str = ""
    branch: str = ""
    base: str = ""
    review_comments: list[str] = field(default_factory=list)
    reviewers: list[str] = field(default_factory=list)
    url: str = ""

    @property
    def needs_fixes(self) -> bool:
        return self.state == "CHANGES_REQUESTED"

    @property
    def is_approved(self) -> bool:
        return self.state == "APPROVED"

    def suggest_converge_criterion(self) -> Optional[str]:
        """Build a converge criterion from review comments when fixes needed."""
        if not self.needs_fixes or not self.review_comments:
            return None
        bullet_list = "\n".join(f"  - {c}" for c in self.review_comments[:10])
        return (
            f"All review comments addressed for PR #{self.pr_number}:\n"
            f"{bullet_list}"
        )

    def to_dict(self) -> dict:
        return {
            "pr_number": self.pr_number,
            "state": self.state,
            "title": self.title,
            "branch": self.branch,
            "base": self.base,
            "review_comments": self.review_comments,
            "reviewers": self.reviewers,
            "url": self.url,
            "needs_fixes": self.needs_fixes,
            "is_approved": self.is_approved,
        }


def _run_gh(args: list[str], timeout: int = 30) -> str:
    """Run a gh CLI command and return stdout."""
    result = subprocess.run(
        ["gh"] + args,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if result.returncode != 0:
        raise RuntimeError(f"gh command failed: {result.stderr.strip()}")
    return result.stdout.strip()


def get_pr_review_status(pr_number: int | str) -> PRReviewStatus:
    """
    Query the review status of a PR via gh CLI.

    Returns a PRReviewStatus with the consolidated review state,
    comments requesting changes, and reviewer names.
    """
    pr_number = int(pr_number)

    # Fetch PR metadata
    pr_json = _run_gh([
        "pr", "view", str(pr_number),
        "--json", "title,headRefName,baseRefName,url,reviewDecision,reviews",
    ])
    pr_data = json.loads(pr_json)

    title = pr_data.get("title", "")
    branch = pr_data.get("headRefName", "")
    base = pr_data.get("baseRefName", "")
    url = pr_data.get("url", "")

    # reviewDecision: APPROVED, CHANGES_REQUESTED, REVIEW_REQUIRED, or ""
    decision = pr_data.get("reviewDecision", "") or "PENDING"

    reviews = pr_data.get("reviews", []) or []
    reviewers = []
    comments = []
    seen_reviewers = set()

    for review in reviews:
        author = review.get("author", {}).get("login", "unknown")
        if author not in seen_reviewers:
            seen_reviewers.add(author)
            reviewers.append(author)

        state = review.get("state", "")
        body = (review.get("body") or "").strip()
        if state == "CHANGES_REQUESTED" and body:
            comments.append(body)

    return PRReviewStatus(
        pr_number=pr_number,
        state=decision,
        title=title,
        branch=branch,
        base=base,
        review_comments=comments,
        reviewers=reviewers,
        url=url,
    )


def get_pr_diff_summary(pr_number: int | str) -> str:
    """Return a compact diff stat for the PR."""
    return _run_gh(["pr", "diff", str(pr_number), "--stat"])


def load_pr_context(pr_number: int | str) -> dict:
    """
    Load full PR context for --from-pr session resume.

    Returns a dict suitable for session state injection with
    review status, diff summary, and converge criterion hint.
    """
    status = get_pr_review_status(pr_number)
    diff_summary = ""
    try:
        diff_summary = get_pr_diff_summary(pr_number)
    except (RuntimeError, subprocess.TimeoutExpired):
        diff_summary = "(diff unavailable)"

    criterion = status.suggest_converge_criterion()

    return {
        "pr_number": status.pr_number,
        "title": status.title,
        "branch": status.branch,
        "base": status.base,
        "url": status.url,
        "review_state": status.state,
        "reviewers": status.reviewers,
        "review_comments": status.review_comments,
        "diff_summary": diff_summary,
        "needs_fixes": status.needs_fixes,
        "is_approved": status.is_approved,
        "converge_criterion": criterion,
    }
