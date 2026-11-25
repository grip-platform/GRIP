"""Robust PR diff fetcher with mergeability-poll + multi-rung fallback ladder.

Public API
----------
robust_pr_diff(pr_number, repo, *, repo_root=None, timeout=60, max_chars=PR_REVIEW_DIFF_BUDGET,
               scrub=None) -> str

Fallback ladder (returns first non-empty stripped result):
  1. Poll gh pr view until mergeable != UNKNOWN (Fibonacci back-off, cap ~30 s).
  2. gh pr diff <N> --repo <R>
  3. gh pr diff <N> --repo <R> --patch
  4. git diff <baseRefName>...<headRefOid>   (local fetch + diff)
  5. "" — caller keeps its existing empty-result guard.

Deleted files
-------------
A deleted file reaches the reviewer as its header (the ``diff --git`` line, its
mode, ``--- a/<path>`` and ``+++ /dev/null``) followed by one line giving how
many lines it removed. Its contents are not included. Everything a deletion
removes is already on the base branch, so a reviewer needs to know which file
goes and how large it was, not every line again; repeating them would send that
content once more to each external provider the review fans out to. A PR that
deletes a safety check still names the file it deletes.

Budget + source-priority
------------------------
A caller's ``scrub`` (AGORA passes the redaction shield) runs on the whole diff
first, so the cut below can never split a name it would have recognised.
When the assembled diff exceeds ``max_chars`` (default PR_REVIEW_DIFF_BUDGET):
  1. Source files are ordered before lock/min/asset/binary/generated hunks.
  2. The result is truncated to ``max_chars``.
  3. An explicit banner is appended so models never hallucinate completeness.
"""
from __future__ import annotations

import json
import re
import subprocess
import time
from typing import Callable, Optional

_FIB_DELAYS = (2, 3, 5, 8, 13)  # total ≤ 31 s

# ~50k tokens — covers virtually every real PR; frontier models handle it.
# Import this constant wherever a diff-budget cap is needed (DRY).
PR_REVIEW_DIFF_BUDGET = 200_000

# File path fragments that sort LAST in the budget window (non-source).
# Source hunks matching none of these come FIRST.
_LOW_PRIORITY_RE = re.compile(
    r"(?:package-lock\.json|yarn\.lock|pnpm-lock\.yaml|Pipfile\.lock|"
    r"Cargo\.lock|poetry\.lock|composer\.lock|Gemfile\.lock|"
    r"\.min\.js|\.min\.css|\.map\b|\.svg\b|\.png\b|\.jpg\b|\.jpeg\b|\.gif\b|"
    r"\.ico\b|\.woff2?\b|\.ttf\b|\.eot\b|\.pdf\b|\.zip\b|\.tar\b|"
    r"dist/|build/|\.next/|__pycache__/|node_modules/|vendor/)"
)

_HUNK_HEADER_RE = re.compile(r"^diff --git ", re.MULTILINE)

# Searched only in a file's header, before its first hunk, so a removed or
# added line that happens to read "deleted file mode" cannot trigger it.
_DELETED_MODE_RE = re.compile(r"^deleted file mode ", re.MULTILINE)


def _source_priority_sort(diff: str) -> str:
    """Re-order diff hunks: source files first, lock/asset/generated last."""
    if not diff:
        return diff
    # Split on 'diff --git ' boundaries; first element may be empty header.
    parts = _HUNK_HEADER_RE.split(diff)
    prefix = parts[0]  # anything before the first hunk (may be "")
    hunks = ["diff --git " + p for p in parts[1:]]

    source_hunks = [h for h in hunks if not _LOW_PRIORITY_RE.search(h)]
    low_hunks = [h for h in hunks if _LOW_PRIORITY_RE.search(h)]
    return prefix + "".join(source_hunks + low_hunks)


def _omit_deleted_body(chunk: str) -> str:
    """One file's diff, with a deleted file's removed lines replaced by their count."""
    head, sep, body = chunk.partition("\n@@")
    if not sep or not _DELETED_MODE_RE.search(head):
        return chunk
    removed = sum(1 for line in body.splitlines() if line.startswith("-"))
    return f"{head}\n[deleted file: {removed} lines removed; contents not sent for review]\n"


def _omit_deleted_bodies(diff: str) -> str:
    """Every file as it was, except that a deleted one keeps only its header and count."""
    parts = _HUNK_HEADER_RE.split(diff)
    return parts[0] + "".join(_omit_deleted_body("diff --git " + p) for p in parts[1:])


def _apply_budget(diff: str, max_chars: int) -> str:
    """Source-prioritise then truncate with an explicit banner if over budget."""
    if len(diff) <= max_chars:
        return diff
    total = len(diff)
    prioritised = _source_priority_sort(diff)
    truncated = prioritised[:max_chars]
    shown = len(truncated)
    omitted = total - shown
    banner = (
        f"\n\n[DIFF TRUNCATED: showing {shown} of {total} chars; "
        f"{omitted} omitted — source files prioritised. "
        f"Treat unseen code as UNKNOWN, do not infer completeness.]"
    )
    return truncated + banner


def _for_review(diff: str, max_chars: int, scrub: Optional[Callable[[str], str]] = None) -> str:
    """What a reviewer receives: deleted contents omitted, then the caller's scrub, then the budget.

    The scrub sees the whole diff. Cutting first could split a name in two, and a
    scrubber recognises neither half, so the first half would leave unscrubbed.
    """
    whole = _omit_deleted_bodies(diff)
    return _apply_budget(scrub(whole) if scrub else whole, max_chars)


def _run(cmd: list[str], *, cwd: Optional[str] = None, timeout: int = 60) -> str:
    """Run a subprocess, return stdout; return '' on any failure."""
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd,
        )
        return proc.stdout if proc.returncode == 0 else ""
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return ""


def _poll_mergeability(pr_number: int, repo: str, timeout: int) -> None:
    """Best-effort poll until GitHub resolves mergeability. Never raises."""
    for delay in _FIB_DELAYS:
        try:
            proc = subprocess.run(
                [
                    "gh", "pr", "view", str(pr_number), "--repo", repo,
                    "--json", "mergeable,mergeStateStatus",
                ],
                capture_output=True, text=True, timeout=timeout, check=False,
            )
            if proc.returncode != 0:
                break
            data = json.loads(proc.stdout)
            if data.get("mergeable", "UNKNOWN") != "UNKNOWN":
                return
        except Exception:  # noqa: BLE001
            break
        time.sleep(delay)


def _base_and_head(view_json: str) -> tuple[str, str]:
    """(baseRefName, headRefOid) from ``gh pr view --json``; ("", "") when unreadable."""
    try:
        data = json.loads(view_json)
        return data.get("baseRefName", ""), data.get("headRefOid", "")
    except Exception:  # noqa: BLE001
        return "", ""


def _local_git_diff(pr_number: int, repo: str, repo_root: Optional[str], timeout: int) -> str:
    """Rung 4: a local ``git diff base...head``; "" when it cannot be computed.

    This is the ONLY rung that can recover a >300-file PR: GitHub's API refuses
    those diffs with HTTP 406 ("diff exceeded the maximum number of files"). The
    CI checkout is a detached HEAD at the PR merge ref, so there is no local
    ``<base>`` branch to diff against, and ``git diff main...head`` fails with
    "unknown revision". So ``origin/<base>`` (fetched here) is tried first, then
    the bare name for a repo whose base is only local.
    """
    view_out = _run(["gh", "pr", "view", str(pr_number), "--repo", repo,
                     "--json", "baseRefName,headRefOid"], timeout=timeout)
    base, head = _base_and_head(view_out) if view_out.strip() else ("", "")
    if not (base and head):
        return ""
    cwd = str(repo_root) if repo_root else None
    _run(["git", "fetch", "--quiet", "origin"], cwd=cwd, timeout=timeout)
    for base_ref in (f"origin/{base}", base):
        out = _run(["git", "diff", f"{base_ref}...{head}"], cwd=cwd, timeout=timeout)
        if out.strip():
            return out
    return ""


def robust_pr_diff(
    pr_number: int,
    repo: str,
    *,
    repo_root: Optional[str] = None,
    timeout: int = 60,
    max_chars: int = PR_REVIEW_DIFF_BUDGET,
    scrub: Optional[Callable[[str], str]] = None,
) -> str:
    """Return the PR diff text via the fallback ladder in the module docstring.

    Deleted files arrive as their header and a line count, never their
    contents. ``scrub``, when given, rewrites the whole diff before the budget
    cuts it. The result is source-prioritised and capped at ``max_chars``;
    if truncated, an explicit banner is appended (never silent).
    """
    _poll_mergeability(pr_number, repo, timeout)
    out = _run(["gh", "pr", "diff", str(pr_number), "--repo", repo], timeout=timeout)
    if not out.strip():
        # Rung 3: the patch endpoint works when a merge-base diff cannot be computed.
        out = _run(["gh", "pr", "diff", str(pr_number), "--repo", repo, "--patch"],
                   timeout=timeout)
    if not out.strip():
        out = _local_git_diff(pr_number, repo, repo_root, timeout)
    return _for_review(out, max_chars, scrub) if out.strip() else ""
