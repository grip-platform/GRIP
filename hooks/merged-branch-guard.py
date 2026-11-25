#!/usr/bin/env python3
"""PreToolUse gate: warn when a `git commit` lands on a branch whose PR already merged.

WARN by default — prints to stderr and audits, NEVER blocks. Promotion to DENY is a
separate, operator-gated step after a WARN-window false-positive rate is measured
(same ladder `no-stubs-gate.py` follows).

WHY WARN AND NOT DENY, specifically here: the only sound signal for a squash-merged
branch comes from the forge, so this gate depends on a network call to `gh`. A gate
that blocked commits whenever GitHub was slow or rate-limited would be worse than the
bug it prevents, and would quickly earn itself a bypass. Every failure path here exits
silently and lets the commit through.

Deadlock check (rules/gate-resolution-escape-hatch.md, Invariant 1): the blocked
condition is "committing to an already-merged branch"; its resolution is
`git checkout -b <new> origin/main`, which is not a commit and so is never gated.
The escape hatch is structurally outside the denied set even if this is later
promoted to DENY.

Detection logic lives in lib/merged_branch_guard.py, which is pure and network-free;
this file only supplies the real `gh` lookup, a cache of answers that record a merge,
and the audit trail.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR") or (Path.home() / ".claude"))

CACHE_PATH = CLAUDE_DIR / "state" / "merged-branch-guard-cache.json"
AUDIT_PATH = CLAUDE_DIR / "logs" / "merged-branch-guard.jsonl"
CACHE_TTL_SECONDS = 900  # how long a "merged" answer is reused; "not merged" never is
# Claude Code kills this hook at 6 s (its registry timeout), so the lookup must give up
# first: then the guard fails open by itself instead of being killed as a hook error.
GH_TIMEOUT_SECONDS = int(os.environ.get("MERGED_BRANCH_GUARD_GH_TIMEOUT", "5"))
AUDIT_MAX_BYTES = 1_000_000  # rotate at ~1 MB; one generation kept alongside

# Matches a real `git commit` invocation. Each case below was found by a test
# rather than by reasoning about the pattern, and the first version got two wrong:
#   `git -C /tmp commit`      MUST match     (global flags with values sit between)
#   `rtk git commit`          MUST match     (GRIP routes git through rtk)
#   `git commit-graph write`  must NOT match (a different subcommand)
#   `git log --grep=commit`   must NOT match (the word as a flag value)
# Requiring whitespace before `commit` rejects the `=commit` case; the negative
# lookahead rejects `commit-graph`. A miss is cheap (no warning), so the pattern
# deliberately errs toward missing.
_GIT_COMMIT = re.compile(r"\bgit\b[^\n;&|]*?\scommit(?![-\w])")

# Quoted runs are blanked before matching, because text inside quotes is data, not
# a command: `echo "git commit"` and `grep "git commit" notes.md` both matched the
# pattern above. That is not merely one wasted gh call, as the original comment
# claimed -- on a branch that really is merged it printed a warning telling the
# operator their commit would proceed, when no commit was happening at all. A
# warning that fires on the wrong thing costs exactly what this hook is for.
#
# Blanking rather than deleting keeps the surrounding tokens apart, so
# `git commit -m "x"` still reads as `git commit -m ""` and still matches. Only
# the quoted content is neutralised; every documented MUST-match case above lives
# outside quotes and is untouched. Unquoted prose (`echo git commit`) still
# matches -- narrowing that further would need a real shell parser, and `shlex`
# cannot be used here: it keys on the first token, which would break both
# `rtk git commit` and `git -C /tmp commit`.
_QUOTED = re.compile(r"'[^']*'|\"[^\"]*\"")


def _strip_quoted(command: str) -> str:
    """Replace the contents of quoted runs with spaces, preserving length."""
    return _QUOTED.sub(lambda m: m.group(0)[0] * 2 + " " * (len(m.group(0)) - 2), command)


def is_git_commit(command: str) -> bool:
    """Does this command actually invoke `git commit`?

    The single predicate the gate consults, exported so the tests exercise the
    decision rather than one of the two pieces it is made of. Asserting on
    `_GIT_COMMIT` alone would keep passing while the gate did something else --
    the wrong-artefact failure that rules/tests-must-be-able-to-fail.md is about.
    """
    return bool(_GIT_COMMIT.search(_strip_quoted(command)))


def _bail() -> None:
    """Every failure path ends here: silent, non-blocking."""
    sys.exit(0)


def _run(args: list[str], timeout: int) -> str | None:
    try:
        proc = subprocess.run(
            args, capture_output=True, text=True, timeout=timeout, check=False
        )
    except (subprocess.SubprocessError, OSError):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.strip()


def _git(*args: str) -> str | None:
    return _run(["git", *args], timeout=3)


def _read_cache() -> dict:
    try:
        return json.loads(CACHE_PATH.read_text())
    except Exception:  # noqa: BLE001
        return {}


def _write_cache(cache: dict) -> None:
    """Write the cache atomically: temp file in the same directory, then rename.

    `os.replace` is atomic on POSIX, so a concurrent reader sees either the old
    file or the new one, never a half-written blob. The previous direct
    `write_text` was not atomic, which AGORA review of #4255 flagged. That is not
    theoretical here: five GRIP sessions were running concurrently on this machine
    the night this was written, all committing, all sharing one cache path. A
    torn write would have been read back as invalid JSON, and while `_read_cache`
    fails safe to an empty dict, the practical result is a cache that never hits.

    The temp file must live in the SAME directory as the target, because
    `os.replace` is only atomic within a single filesystem.
    """
    try:
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(CACHE_PATH.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as fh:
                json.dump(cache, fh)
            os.replace(tmp, CACHE_PATH)
        except Exception:  # noqa: BLE001
            with contextlib.suppress(OSError):
                os.unlink(tmp)
            raise
    except Exception:  # noqa: BLE001
        pass


def _gh_lookup(branch: str) -> list:
    """Ask the forge for merged PRs on this branch, cached BY BRANCH NAME.

    The first version keyed this cache on (branch, head_sha), with the rationale
    that "a moved tip re-queries, which is exactly the transition we care about".
    That reasoning was wrong, and AGORA review of #4255 caught it. This hook runs
    BEFORE each commit, so the tip differs on every single invocation -- the key was
    unique every time, the cache never hit, and every commit on any branch that ever
    had a PR merged made a fresh network call. The TTL was decorative and the thing
    was a rate-limit generator.

    The tip belongs in the COMPARISON, not the LOOKUP. What the lookup answers is
    "does this branch have a merged PR", which changes only when a PR merges. That
    is rare, but the answer flips at exactly the moment this guard matters, so only
    a "merged" answer is reused (`_cached_merged_answer`, GRIP#6980). The caller
    still compares the live head_sha with the cached PR's headRefOid, so the magnet
    transition is still detected on the very next commit.
    """
    cached = _cached_merged_answer(branch)
    if cached is not None:
        return cached
    prs = _fetch_merged_prs(branch)
    _cache_if_merged(branch, prs)
    return prs


def _cached_merged_answer(branch: str) -> list | None:
    """The cached answer for `branch`, served only while fresh AND only if it records a merge.

    The cache used to keep "no merged PR" for the full TTL too, so for up to 15
    minutes after a merge the guard answered from a lookup made while the PR was
    still open -- the very window in which this mistake happens (GRIP#6980).
    GRIP#6959 merged at 23:54:40 UTC on 2026-10-01; its entry, cached at 23:46:23,
    stayed valid until 00:01:23, and three commits landed inside it unwarned.
    Checking here, on the read side, also retires a "not merged" entry that an
    older version of this hook wrote.
    """
    hit = _read_cache().get(branch)
    if not isinstance(hit, dict) or time.time() - hit.get("ts", 0) >= CACHE_TTL_SECONDS:
        return None
    prs = hit.get("prs") or []
    return prs if _records_merge(prs) else None


def _fetch_merged_prs(branch: str) -> list:
    """One live `gh` lookup. Raises when the forge cannot answer; the caller fails open."""
    out = _run(
        [
            "gh", "pr", "list",
            "--head", branch,
            "--state", "merged",
            "--json", "number,state,mergedAt,headRefOid",
            "--limit", "10",
        ],
        timeout=GH_TIMEOUT_SECONDS,
    )
    if out is None:
        raise RuntimeError("gh lookup unavailable")
    return json.loads(out) if out else []


def _cache_if_merged(branch: str, prs: list) -> None:
    """Remember an answer only if it records a merge, which is permanent.

    "Not merged yet" can change at any moment, so an unmerged branch is asked about
    on every commit. That costs one `gh` call, 0.6 to 0.85 s measured on 2026-10-02,
    inside the hook's 6 s timeout.
    """
    if _records_merge(prs):
        cache = _read_cache()
        cache[branch] = {"ts": time.time(), "prs": prs}
        _write_cache(cache)


def _records_merge(prs: list) -> bool:
    """`has_merged_pr` from lib/: the cache's test for "merged" is the decision's own.

    Imported here, not at module scope. lib/ joins sys.path only inside main(), and
    merely importing this file must not change sys.path (see main() and
    test_importing_the_hook_does_not_mutate_sys_path), so do not hoist this import.
    """
    from merged_branch_guard import has_merged_pr

    return has_merged_pr(prs)


def _rotate_audit_if_large() -> None:
    """Keep the audit trail bounded.

    Raised by AGORA review of PR #4255 (quote-verified). The original appended
    without any cap. Each line is roughly 120 bytes and only a genuine warning
    writes one, so unbounded growth was slow rather than dramatic -- but "slow" is
    not "bounded", and an append-only file on a developer machine has no natural
    end. One rotation keeps the current plus the previous generation, which is
    enough to investigate a recent incident without ever exceeding ~2 MB.
    """
    try:
        if AUDIT_PATH.exists() and AUDIT_PATH.stat().st_size > AUDIT_MAX_BYTES:
            AUDIT_PATH.replace(AUDIT_PATH.with_suffix(".jsonl.1"))
    except OSError:
        pass


def _audit(verdict) -> None:
    try:
        AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
        _rotate_audit_if_large()
        with AUDIT_PATH.open("a") as fh:
            fh.write(json.dumps({
                "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "branch": verdict.branch,
                "status": verdict.status,
                "merged_pr": verdict.merged_pr,
                "mode": "warn",
            }) + "\n")
    except Exception:  # noqa: BLE001
        pass


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except Exception:  # noqa: BLE001
        _bail()

    if payload.get("tool_name") != "Bash":
        _bail()
    command = str(payload.get("tool_input", {}).get("command", ""))
    if not is_git_commit(command):
        _bail()

    branch = _git("rev-parse", "--abbrev-ref", "HEAD")
    head_sha = _git("rev-parse", "HEAD")
    if not branch or branch == "HEAD" or not head_sha:
        _bail()  # detached HEAD or not a repo

    # Injected here, not at module scope: importing this file (a test collecting
    # hooks/, a linter, anything that touches it without running it) should not
    # permanently reorder module resolution for the whole process. Raised by AGORA
    # review of PR #4255. The hook itself is unaffected -- it only ever reaches this
    # line by running -- so the change costs nothing and removes an import-time
    # side effect from a file whose entire job is to be side-effect-free until it
    # has a reason to warn.
    lib_dir = str(CLAUDE_DIR / "lib")
    if lib_dir not in sys.path:
        sys.path.insert(0, lib_dir)

    try:
        from merged_branch_guard import evaluate_branch
    except Exception:  # noqa: BLE001
        _bail()

    verdict = evaluate_branch(branch, head_sha, _gh_lookup)
    if not verdict.should_warn:
        _bail()

    _audit(verdict)
    print(
        f"[merged-branch-guard] WARNING: {verdict.reason}\n"
        f"  Branch: {verdict.branch}\n"
        f"  This is a warning only — your commit will proceed.\n"
        f"  To start clean:  git checkout -b <new-branch> origin/main",
        file=sys.stderr,
    )
    sys.exit(0)


if __name__ == "__main__":
    main()
