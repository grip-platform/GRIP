"""AGORA verdict router — pure functions for verdict extraction + action routing.

Origin: 2026-05-26. V>>'s 2nd ask after PR #3244 fix: "set up a true /loop to
make sure all CI Convergence for AGORA reviews on PRs across all repositories
where it is enabled, runs without fault."

This module is the PURE-FUNCTION CORE of the AGORA convergence loop. It does
NO I/O — no gh calls, no file writes, no subprocess. All state comes in as
arguments; all decisions come out as return values. That makes it Goodhart-
resistant testable: every output is reproducible from input alone.

The bash tick wrapper (`bin/agora-convergence-tick`) does the I/O — calls
gh for PR/comment data, then feeds the data through these functions, then
takes the routed action (workflow_dispatch / log / notify).

Verdict format (observed on real AGORA comments, e.g. PR #3244 2026-05-26):

    ## AGORA Council Review

    > **Verdict: REQUEST CHANGES**
    > *Recommended by LLM-AS-JUDGE ...*

Note: real AGORA comments use SPACES ("REQUEST CHANGES"), while
rules/ci-review-driven-convergence.md documents UNDERSCORES
("REQUEST_CHANGES"). This parser normalises both to underscore form.

Action mapping (rules/ci-review-driven-convergence.md verdict-gated
convergence + rules/exogenous-anchor-law.md RSI-loop merge carve-out
V>>-ratified 2026-05-17 / 2026-05-26):

| Verdict          | Action label  | Tick behaviour                                       |
|------------------|---------------|------------------------------------------------------|
| MERGE            | auto_merge    | `gh pr merge --admin --squash` if eligible (see below) |
| REQUEST_CHANGES  | loop_fixes    | Log; operator/RSI loop drives fix loop               |
| DEFER_CHANGES    | defer         | Log; AGORA auto-creates sprint-loop issue            |
| FAIL/ERROR/None  | rerun         | `gh workflow run` if stale, else wait                |

AGORA PASS == exogenous ratification (V>> 2026-05-26): the cross-provider
review (3 speakers + LLM-as-judge exogenous to speakers) is itself the
exogenous anchor per `rules/exogenous-anchor-law.md`. The loop admin-bypass
auto-merges on MERGE verdict + eligibility; the human merge is *sufficient*
but not *required* as the anchor.

ELIGIBILITY (auto_merge_eligible(), all 3 must hold — the carve-out):
  (a) verdict == MERGE
  (b) CI checks green (caller checks via `gh pr checks --json`)
  (c) PR NOT bootstrap-excepted — does not modify the convergence loop
      itself or its governing rules (loop granting itself authority then
      using it to ratify the grant = the self-fulfilling failure the law
      forbids).

Falsification: parser is wrong if it (a) produces a verdict for a non-AGORA
comment, (b) picks an older AGORA comment when a newer one exists, or
(c) returns None on a well-formed verdict line. Eligibility is wrong if it
ever returns True for a bootstrap-excepted PR (the kill-switch the law
mandates). Each is tested in tests/test_agora_verdict_router.py.
"""

from __future__ import annotations

import re
from typing import Any

# Verdict tokens AGORA emits, normalised to underscore form internally.
_VERDICT_TOKENS = {
    "MERGE",
    "REQUEST_CHANGES",
    "DEFER_CHANGES",
    "FAIL",
    "ERROR",
    "HALLUCINATION",
    # #4058 Defect 2: the machine-readable judge-unavailable token the producer
    # now renders (lib/agora_pr_review.py::VERDICT_JUDGE_UNAVAILABLE) instead of
    # free English prose. Recognised here so the token is never mistaken for an
    # unknown/garbage verdict; parse_verdict() maps it to the fail-CLOSED
    # REQUEST_CHANGES fallback below.
    "JUDGE_UNAVAILABLE",
}

# The producer's judge-unavailable token + the fallback every consumer maps it
# to. Mirrors lib/agora_pr_review.py (VERDICT_JUDGE_UNAVAILABLE /
# VERDICT_JUDGE_UNAVAILABLE_FALLBACK) — kept as literals so this module stays
# import-free and pure.
JUDGE_UNAVAILABLE_TOKEN = "JUDGE_UNAVAILABLE"
JUDGE_UNAVAILABLE_FALLBACK = "REQUEST_CHANGES"

# Action labels — the tick wrapper switches on these to decide what to do.
# Renamed merge_ready → auto_merge per V>> 2026-05-26: AGORA PASS itself is
# exogenous ratification per rules/exogenous-anchor-law.md; no human merge
# required for autonomous-loop PRs (eligibility-gated by auto_merge_eligible).
ACTION_AUTO_MERGE = "auto_merge"
ACTION_LOOP_FIXES = "loop_fixes"
ACTION_DEFER = "defer"
ACTION_RERUN = "rerun"
ACTION_WAIT = "wait"  # no AGORA comment yet (CI still running)

# Bootstrap-excepted paths — PRs that modify these MUST NOT be auto-merged by
# the convergence loop, because the loop would be ratifying its own authority.
# Rule: rules/exogenous-anchor-law.md "Bootstrap exception (non-negotiable)".
# Each entry is a PREFIX of the file paths returned by
# `gh api repos/<o>/<r>/pulls/<n>/files --jq '.[].filename'`, so "lib/agora_"
# covers AGORA's own verdict code and ".github/" every workflow.
#
# This is the one list of merge-machinery paths (GRIP#7927). AGORA's ladder
# reads it (lib/agora_ladder_shadow.py, _VERDICT_PATH_PREFIXES), and
# bin/grip-pr-monitor's MERGE_MACHINERY_RE must equal it, which
# tests/test_grip_pr_monitor.py checks. Three separate lists had drifted: each
# held paths the others missed, so one merger could merge what another refused.
#
# What belongs here: code that merges a PR or decides whether one may merge,
# and a document that is the authoritative statement of who may merge without a
# human or of when an automated merger merges or holds. A document that only
# cites or applies those rules does not. A skill or agent definition that
# tells an agent to run a merge counts as a merger. Code and instructions that
# merge are found mechanically (tests/test_grip_pr_monitor.py scans for merge
# calls); whether a document grants authority is a judgement, and only the
# harness instruction files are checked.
BOOTSTRAP_EXCEPTED_PATHS = frozenset({
    # The gates that govern autonomous merge, which the bootstrap exception
    # names: AGORA's verdict code, the transport its council uses, the diff it
    # is shown, the CI workflows, the sweep that merges on its verdict, the
    # other script that admin-merges on green, and the gate that refuses a merge
    # removing a guardrail.
    "lib/agora_",
    "lib/hal_council_dispatch.py",
    "lib/pr_diff.py",
    ".github/",
    "bin/grip-pr-monitor",
    "bin/grip-merge-when-green",
    "hooks/guardrail-removal-merge-gate.py",
    "lib/guardrail_removal_detector.py",
    # The red-check rule for a session's own merge (V>> 2026-10-17): the gate
    # that denies a merge past the PR's own red, and the judgement it and the
    # Stop-hook merger share with the sweep.
    "hooks/red-check-merge-gate.py",
    "lib/red_check.py",
    # The teammate merger: a WhatsApp approval ends in an admin merge, and its
    # config decides who may approve and where (Codex, GRIP#7927).
    "lib/team_ratification.py",
    "config/team-ratification.json",
    # The trust root of the always-on tick's signed act grants (GRIP#8821, the
    # operator's decision 2): whoever controls this key decides who may authorise
    # the tick's act, so the operator merges every change to it.
    "config/tick/grant-verifying.pub",
    # The contract that pins every restatement of the merge gate to canon, so a
    # skill such as /finish cannot diverge without touching a held file.
    "tests/test_verdict_polarity_contract.py",
    # Instructions an agent follows to merge a PR (Codex, GRIP#7927): editing
    # one changes when that agent merges, so the edit is held too.
    "agents/rsi-orchestrator.md",
    "agents/sprint-loop-agent.md",
    "skills/auto/SKILL.md",
    "skills/deploying-grip-to-projects/SKILL.md",
    "skills/finish/SKILL.md",
    "skills/pr-automation/reference.md",
    "skills/sprint-loop/RUNBOOK.md",
    "skills/triage/SKILL.md",
    "skills/ultrado/SKILL.md",
    # The overnight-wave prompts and the AIDE autonomous workflow carry an
    # admin-merge step too.
    "config/overnight-wave-prompt.md",
    "config/overnight-wave-prompt-opencode.md",
    "data/aide/workflows/rsi-autonomous.yaml",
    # When the sweep merges and when it holds (Codex, GRIP#7927).
    "rules/cross-harness-background-monitor.md",
    "bin/agora-convergence-tick",
    "lib/agora_verdict_router.py",
    "state/agora-convergence-repos.json",
    "rules/ci-review-driven-convergence.md",
    "rules/exogenous-anchor-law.md",
    "tests/test_agora_verdict_router.py",
    # The tick's own regression test (GRIP#9068). Until Codex caught it on
    # GRIP#9124 this entry named a .sh that never existed, so the real test
    # was unprotected.
    "tests/test_agora_convergence_tick.py",
    # The merge authority is WRITTEN in these three, so they belong here as
    # much as the loop's own code does (GRIP#6654). CLAUDE.md carries the
    # PARAMOUNT "AGORA-Green = Merge - Don't Ask" block and the stuck-loop
    # admin-merge carve-out; .claude/CLAUDE.md is the project copy every
    # session in this repo loads; in-session-agora-convergence.md relocates
    # the same authority to repos with no installed AGORA. A PR that edits only
    # them used to pass this guard, so the loop could ratify a rewrite of its
    # own merge authority - the exact self-grant the bootstrap exception bars.
    "CLAUDE.md",
    ".claude/CLAUDE.md",
    "rules/in-session-agora-convergence.md",
    # Rules 6 and 7 (merge without asking; the stuck-loop admin merge) grant
    # the same authority, and Codex found them missing here on GRIP#7927.
    "rules/operational-principles.md",
    # The instruction file every non-Claude harness loads states both grants
    # too (Codex, GRIP#7927).
    "AGENTS.md",
})

# Regex to find the verdict line. Tolerates leading whitespace, the blockquote
# marker `>`, and either spaces or underscores inside the token.
#
# Matches:  > **Verdict: MERGE**
#           > **Verdict: REQUEST CHANGES**
#           >**Verdict:REQUEST_CHANGES**
# Rejects:  Verdict: MERGE       (no blockquote / no bold delimiters)
#           > **Verdict:**        (no token)
_VERDICT_RE = re.compile(
    r"^\s*>\s*\*\*\s*Verdict\s*:\s*([A-Z][A-Z_\s]*?)\s*\*\*",
    re.MULTILINE,
)

# Sentinel for AGORA comment identification — the H1 heading bot uses.
_AGORA_HEADER = "## AGORA Council Review"

# GitHub Actions bot login (case-sensitive in API responses).
_BOT_LOGIN = "github-actions[bot]"


# ---------------------------------------------------------------------------
# #4058 — head-SHA idempotency stamp + judge-unavailable fail-closed.
# ---------------------------------------------------------------------------

# Machine-readable marker the AGORA render stamps into EVERY comment
# (lib/agora_pr_review.py::AgoraReview.to_markdown) so any dispatcher can tell
# WHICH commit a verdict reviewed and skip re-reviewing an unchanged diff.
# Invisible in the rendered comment (an HTML comment). THE single source of
# truth for the marker: both the render (producer) and the matcher below build
# it via head_sha_marker() (DRY — two independent literals would drift silently
# and break the guard with zero test failure).
HEAD_SHA_MARKER_PREFIX = "<!-- agora-head-sha: "
_HEAD_SHA_MARKER_SUFFIX = " -->"


def head_sha_marker(head_sha: str) -> str:
    """The exact invisible comment marker for ``head_sha`` (render == matcher)."""
    return f"{HEAD_SHA_MARKER_PREFIX}{head_sha}{_HEAD_SHA_MARKER_SUFFIX}"


# The "Judge unavailable" banner rendered by
# lib/agora_pr_review.py::_render_verdict_block when judge_valid is False.
# The producer's own VERDICT_JUDGE_UNAVAILABLE_FALLBACK = REQUEST_CHANGES (and
# its docstring: "so CI-driven-convergence loops ... rather than silently merges
# on judge failure") require this map to REQUEST_CHANGES — NOT None. None lets
# latest_agora_verdict() skip the comment and fall back to an OLDER, possibly-
# MERGE verdict: a silent fail-OPEN on judge failure (issue #4058). Anchored to
# the verdict blockquote so a Speaker merely *mentioning* "judge unavailable" in
# prose is never mis-read as the verdict.
_JUDGE_UNAVAILABLE_RE = re.compile(
    r"^\s*>\s*\*\*\s*Verdict\s*:\s*Judge\s+unavailable",
    re.MULTILINE | re.IGNORECASE,
)


def _normalise_verdict(raw: str) -> str | None:
    """Uppercase + collapse internal whitespace to single underscores.

    Returns None if the normalised token is not in the known verdict set —
    prevents bogus tokens from being treated as valid verdicts.
    """
    if not raw:
        return None
    token = re.sub(r"\s+", "_", raw.strip().upper())
    return token if token in _VERDICT_TOKENS else None


def parse_verdict(body: str) -> str | None:
    """Extract the verdict from a single AGORA comment body.

    Returns the normalised verdict token (e.g. "MERGE", "REQUEST_CHANGES")
    or None if the body is not a well-formed AGORA verdict comment.

    The body must (a) start with (or contain) the canonical "## AGORA Council
    Review" header AND (b) contain a `> **Verdict: <TOKEN>**` blockquote.
    Both gates fire — a comment that has the header but no verdict returns
    None (the comment is in-flight, not yet decided).
    """
    if not body or _AGORA_HEADER not in body:
        return None
    # #4058 Defect 2: the "Judge unavailable" banner does NOT match _VERDICT_RE
    # (its lowercase prose can't be captured), so today it returns None and is
    # silently dropped — letting latest_agora_verdict() fall back to an OLDER,
    # possibly-MERGE verdict (a fail-OPEN on judge failure). Map it to the
    # documented REQUEST_CHANGES fallback BEFORE the token regex so a judge
    # failure fails CLOSED (loop, never a stale-MERGE fall-through), honouring
    # lib/agora_pr_review.py::VERDICT_JUDGE_UNAVAILABLE_FALLBACK.
    if _JUDGE_UNAVAILABLE_RE.search(body):
        return JUDGE_UNAVAILABLE_FALLBACK
    match = _VERDICT_RE.search(body)
    if not match:
        return None
    verdict = _normalise_verdict(match.group(1))
    # #4058 Defect 2: the machine-readable token maps to the SAME fail-closed
    # fallback as the legacy prose banner, so both comment vintages route
    # identically (loop, never a stale-MERGE fall-through).
    if verdict == JUDGE_UNAVAILABLE_TOKEN:
        return JUDGE_UNAVAILABLE_FALLBACK
    return verdict


def latest_agora_verdict(comments: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Find the newest AGORA comment in a PR's comment list + extract its verdict.

    `comments` is the raw list from `gh api repos/<o>/<r>/issues/<n>/comments`.
    Each item must have `user.login`, `created_at`, `body`.

    Returns a dict {verdict, created_at, body, comment_id} for the newest
    AGORA comment with a parseable verdict, or None if none exist.

    Sorting: AGORA may post multiple comments over a PR's life (re-runs,
    new commits). The NEWEST one wins — older ones are stale telemetry.
    """
    if not comments:
        return None
    candidates = []
    for c in comments:
        # Defensive: tolerate missing fields rather than KeyError.
        user = (c.get("user") or {}).get("login", "")
        if user != _BOT_LOGIN:
            continue
        body = c.get("body") or ""
        verdict = parse_verdict(body)
        if verdict is None:
            continue
        candidates.append({
            "verdict": verdict,
            "created_at": c.get("created_at", ""),
            "body": body,
            "comment_id": c.get("id"),
        })
    if not candidates:
        return None
    # Sort by created_at descending (ISO-8601 timestamps sort lexicographically).
    candidates.sort(key=lambda x: x["created_at"], reverse=True)
    return candidates[0]


def route_action(verdict: str | None) -> str:
    """Map a verdict (or None) to an action label the tick wrapper acts on.

    Pure function — no side effects. The wrapper decides what to DO with each
    action label; this just classifies.

    None / unknown verdict → ACTION_WAIT (don't rerun, don't act — likely
    AGORA hasn't posted yet OR the PR opened too recently). The wrapper's
    rerun heuristic is age-based (rerun only if no AGORA comment after N
    minutes), implemented in the tick — not here, to keep this pure.
    """
    if verdict == "MERGE":
        return ACTION_AUTO_MERGE
    if verdict == "REQUEST_CHANGES":
        return ACTION_LOOP_FIXES
    if verdict == "DEFER_CHANGES":
        return ACTION_DEFER
    if verdict in ("FAIL", "ERROR", "HALLUCINATION"):
        return ACTION_RERUN
    # Unknown / missing → wait (let the next tick re-check).
    return ACTION_WAIT


def is_bootstrap_excepted(changed_paths: list[str] | str) -> bool:
    """Return True if the PR modifies the convergence loop itself or its rules.

    Per rules/exogenous-anchor-law.md "Bootstrap exception (non-negotiable)":
    a PR that creates or widens THIS merge authority must NOT be merged BY
    the loop. The loop ratifying its own authority is the self-fulfilling
    failure the law exists to prevent.

    `changed_paths` is the list returned by
        gh api repos/<o>/<r>/pulls/<n>/files --jq '[.[].filename]'
    A bare string is read as one path per line, the way
        gh api repos/<o>/<r>/pulls/<n>/files --jq '.[].filename'
    prints them; blank text counts as no paths, so it fails safe-closed.
    """
    if isinstance(changed_paths, str):
        # A string is iterable, so without this each character was tested as a
        # path, nothing matched, and an excepted file read as not excepted.
        changed_paths = [p.strip() for p in changed_paths.splitlines() if p.strip()]
    if not changed_paths:
        # Defensive: empty / missing file list — refuse to auto-merge.
        # A failed gh-api call should fail-safe-closed.
        return True
    prefixes = tuple(BOOTSTRAP_EXCEPTED_PATHS)
    return any(p.startswith(prefixes) for p in changed_paths)


def auto_merge_eligible(
    verdict: str | None,
    ci_checks_green: bool,
    changed_paths: list[str],
) -> tuple[bool, str]:
    """Decide whether a MERGE-verdict PR is eligible for admin-bypass auto-merge.

    Returns (eligible, reason). `reason` is a short slug suitable for telemetry
    and operator-facing logs.

    All 3 conditions per rules/exogenous-anchor-law.md RSI-loop merge carve-out
    must hold:
      (a) verdict == "MERGE"
      (b) ci_checks_green is True (caller verifies via gh pr checks)
      (c) PR is NOT bootstrap-excepted

    Defensive: any single condition failing returns (False, "<reason>") with a
    specific reason — never a silent True. Fail-closed.
    """
    if verdict != "MERGE":
        return (False, f"verdict_not_merge:{verdict or 'none'}")
    if not ci_checks_green:
        return (False, "ci_not_green")
    if is_bootstrap_excepted(changed_paths):
        return (False, "bootstrap_excepted")
    return (True, "all_eligible")


def should_rerun_stale(
    pr_opened_at: str,
    now_iso: str,
    latest_agora: dict | None,
    stale_after_minutes: int = 30,
) -> bool:
    """Decide whether to workflow_dispatch a rerun for a PR with NO AGORA verdict.

    Args:
        pr_opened_at: ISO-8601 timestamp of PR creation.
        now_iso: ISO-8601 timestamp of the current tick (caller-supplied for
            pure-function testability — no `datetime.now()` here).
        latest_agora: result of latest_agora_verdict() — None if no AGORA
            comment exists.
        stale_after_minutes: minutes since PR open after which we rerun if
            still no AGORA verdict. Default 30 — AGORA normally posts within
            ~5min of PR open; 30min means something went wrong.

    Returns True iff a rerun is warranted (PR is old enough, no AGORA yet).

    Pure function; no I/O. Caller (the tick wrapper) does the gh workflow run.
    """
    if latest_agora is not None:
        # AGORA HAS posted — no need to rerun via this path. Rerun for
        # FAIL/ERROR verdicts is handled by route_action()→ACTION_RERUN.
        return False
    if not pr_opened_at or not now_iso:
        return False
    try:
        from datetime import datetime
        opened = datetime.fromisoformat(pr_opened_at.replace("Z", "+00:00"))
        now = datetime.fromisoformat(now_iso.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return False
    age_minutes = (now - opened).total_seconds() / 60
    return age_minutes >= stale_after_minutes


def _agora_comment_reviews_sha(body: str, head_sha: str) -> bool:
    """True iff `body` is an AGORA comment stamped for exactly `head_sha`.

    Pure string check against the full marker (``head_sha_marker``): the render
    stamps ``<!-- agora-head-sha: <full-40-char-sha> -->`` into every comment.
    Exact full-SHA match (prefix + sha + suffix), never a truncated prefix — a
    short-prefix compare could re-suppress review of a genuinely different
    commit sharing a prefix.
    """
    if not body or not head_sha or _AGORA_HEADER not in body:
        return False
    return head_sha_marker(head_sha) in body


def genuine_verdict_for_sha(
    comments: list[dict[str, Any]], head_sha: str,
) -> str | None:
    """Verdict of the newest AGORA comment that GENUINELY reviewed `head_sha`.

    "Genuine" = the judge actually produced a verdict for this exact commit —
    it EXCLUDES the judge-unavailable banner. parse_verdict() maps that banner
    to REQUEST_CHANGES for the merge-gate (fail-closed), but a judge-unavailable
    review is a RETRYABLE infra failure, not a settled review of the diff: the
    same SHA SHOULD be re-reviewed when the judge recovers. So the idempotency
    key deliberately does not count it.

    Returns the normalised verdict token (MERGE / REQUEST_CHANGES /
    DEFER_CHANGES) of the newest genuine, SHA-stamped, bot-authored AGORA
    verdict, or None if none exists.

    Pure function. `head_sha` is the current PR head SHA (full 40 chars);
    `comments` is the raw gh-api issue-comments list. This is the caller-
    agnostic idempotency primitive for issue #4058 — used by the AGORA
    producer (lib/agora_pr_review.py::main) to skip re-reviewing an unchanged
    diff regardless of who dispatched it (cron tick, manual `gh workflow run`,
    or the overnight converge wave).
    """
    if not head_sha or not comments:
        return None
    candidates = []
    for c in comments:
        if (c.get("user") or {}).get("login", "") != _BOT_LOGIN:
            continue
        body = c.get("body") or ""
        if not _agora_comment_reviews_sha(body, head_sha):
            continue
        # Judge-unavailable is retryable, not a settled verdict — never an
        # idempotency key (else a transient judge outage permanently freezes
        # the diff at REQUEST_CHANGES with no path to re-review).
        if _JUDGE_UNAVAILABLE_RE.search(body):
            continue
        match = _VERDICT_RE.search(body)
        verdict = _normalise_verdict(match.group(1)) if match else None
        # #4058: the token form is retryable infra failure, exactly like the
        # legacy prose banner skipped above — never an idempotency key, or a
        # transient judge outage would freeze the SHA with no path to
        # re-review when the judge recovers.
        if verdict is None or verdict == JUDGE_UNAVAILABLE_TOKEN:
            continue
        candidates.append((c.get("created_at", ""), verdict))
    if not candidates:
        return None
    # Newest genuine verdict wins (ISO-8601 timestamps sort lexicographically).
    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates[0][1]


def verdict_exists_for_sha(comments: list[dict[str, Any]], head_sha: str) -> bool:
    """True iff a GENUINE AGORA verdict already reviewed this exact head SHA.

    Thin bool wrapper over genuine_verdict_for_sha() — the idempotency
    predicate a dispatcher checks before (re-)reviewing an unchanged diff.
    False when no comment carries the SHA stamp (pre-stamp comments, or the
    render change not yet shipped) OR the only stamped review was
    judge-unavailable: callers MUST treat False as "cannot prove
    already-reviewed" and fall through to their normal path, never as licence
    to loop.
    """
    return genuine_verdict_for_sha(comments, head_sha) is not None
