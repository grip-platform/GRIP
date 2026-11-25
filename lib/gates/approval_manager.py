"""Approval Manager - Destructive action approval CRUD with file locking.

Extracted from hooks/context-gate-hook.py (#933).
Continues the pattern established by bash_gates.py, git_gates.py, security_gates.py.
"""

import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

# Ensure lib/ is in path for FileLock import
sys.path.insert(0, str(Path.home() / ".claude" / "lib"))
from file_lock import FileLock, LockTimeoutError

from .patterns import (
    APPROVAL_EXPIRY_SECONDS,
    ACTION_SUPERSETS,
    ACTION_EXPIRY_OVERRIDES,
    ACTION_CODE_WRITE,
    ACTION_DEPLOY,
    ACTION_FORCE_PUSH,
    ACTION_GIT_PUSH,
    ACTION_TAILSCALE_SSH,
    CODE_WRITE_APPROVAL_KEYWORDS,
    CODE_WRITE_BATCH_KEYWORDS,
)

CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR_OVERRIDE", str(Path.home() / ".claude")))
APPROVAL_FILE = CLAUDE_DIR / "state" / "approvals" / "destructive-approval.json"

# Logging function - set by hook at import time via configure_logging()
_log_hook = None

# Cached instance UUID (avoids repeated subprocess/file reads per hook call)
_cached_instance_uuid: str | None = None

# Action aliases for normalisation (imported lazily to avoid circular deps)
_ACTION_ALIASES = None


def configure_logging(log_fn):
    """Set the logging function used by this module.

    Called by the hook after import to wire up log_hook.
    """
    global _log_hook
    _log_hook = log_fn


def _log(level: str, message: str):
    """Internal log helper that delegates to configured log_hook."""
    if _log_hook:
        _log_hook(level, message)


def _get_action_aliases():
    """Lazy-load ACTION_ALIASES to avoid import-time circular dependencies."""
    global _ACTION_ALIASES
    if _ACTION_ALIASES is None:
        from .patterns import ACTION_ALIASES
        _ACTION_ALIASES = ACTION_ALIASES
    return _ACTION_ALIASES


def _normalize_action_type(raw: str) -> str:
    """Normalize free-form action string to canonical key.

    Handles legacy formats ("force push", "git push") and new canonical keys.
    """
    key = raw.strip().lower()
    aliases = _get_action_aliases()
    if key in aliases:
        return aliases[key]
    # Fallback: replace spaces/hyphens with underscores
    return key.replace(" ", "_").replace("-", "_")


def _get_instance_uuid() -> str:
    """Get the per-instance UUID for this Claude Code session.

    Walks the process tree to find the Claude Code grandparent PID,
    then reads /tmp/grip-instance-{pid}.id. Result is cached in a
    module-level variable to avoid repeated lookups.
    """
    global _cached_instance_uuid
    if _cached_instance_uuid is not None:
        return _cached_instance_uuid

    try:
        # Hook runs as: Claude Code (node) -> shell -> python3 (this script)
        # os.getppid() = shell, parent of shell = Claude Code
        import subprocess
        shell_pid = os.getppid()
        result = subprocess.run(
            ["ps", "-o", "ppid=", "-p", str(shell_pid)],
            capture_output=True, text=True, timeout=2
        )
        grandparent_pid = result.stdout.strip()
        if grandparent_pid:
            from _compat import grip_instance_path
            id_file = Path(grip_instance_path(grandparent_pid))
            if id_file.exists():
                _cached_instance_uuid = id_file.read_text().strip()
                return _cached_instance_uuid
    except Exception as _e:
        _log("DEBUG", f"Instance UUID lookup failed: {_e}")

    _cached_instance_uuid = ""
    return ""


def _load_approvals() -> dict:
    """Load multi-approval dict from file, handling legacy single-object format.

    Returns: {"approvals": {"action_key": {"timestamp": ..., "expiry_seconds": ...}, ...}}
    """
    if not APPROVAL_FILE.exists():
        return {"approvals": {}}

    try:
        data = json.loads(APPROVAL_FILE.read_text())
    except (json.JSONDecodeError, OSError) as e:
        _log("ERROR", f"Failed to read approval file: {e}")
        return {"approvals": {}}

    # New multi-approval format
    if "approvals" in data and isinstance(data["approvals"], dict):
        return data

    # Legacy single-object format: {"action": "...", "timestamp": "..."}
    if "action" in data and "timestamp" in data:
        canonical = _normalize_action_type(data["action"])
        entry = {"timestamp": data["timestamp"]}
        if "expiry_seconds" in data:
            entry["expiry_seconds"] = data["expiry_seconds"]
        return {"approvals": {canonical: entry}}

    return {"approvals": {}}


def _save_approvals(store: dict):
    """Write multi-approval dict to file."""
    try:
        APPROVAL_FILE.parent.mkdir(parents=True, exist_ok=True)
        APPROVAL_FILE.write_text(json.dumps(store))
    except OSError as e:
        _log("ERROR", f"Failed to write approval file: {e}")


def _locked_approval_operation(operation):
    """Execute an approval file operation under exclusive file lock (TOCTOU fix).

    Uses FileLock with timeout to ensure atomic read-modify-write on the approval file.
    The lock file is separate from the data file to avoid corruption.
    """
    lock_path = APPROVAL_FILE.parent / ".destructive-approval.lock"

    try:
        with FileLock(lock_path, timeout=5):
            return operation()
    except OSError as e:
        _log("ERROR", f"Approval lock failed: {e}")
        return operation()


def _is_approval_valid(entry: dict, now: datetime = None, action_type: str = None) -> bool:
    """Check if a single approval entry is still within its expiry window.

    Resolution order for TTL:
    1. Per-entry `expiry_seconds` field (set explicitly at grant time)
    2. Per-action override from ACTION_EXPIRY_OVERRIDES (covers legacy entries
       written before per-action TTLs existed — they get the same headroom as
       newly-granted ones, no migration needed)
    3. APPROVAL_EXPIRY_SECONDS default (600s for unknown action types)

    Origin: 2026-05-25 — V>>'s tailscale_ssh approval expired mid-CryptoMondays-event
    between successive scp deploys. Old entries lacked `expiry_seconds`, so they
    used the 600s default even though the action-specific override (14400s for
    tailscale_ssh) should have applied.
    """
    try:
        ts = datetime.fromisoformat(entry["timestamp"])
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        if now is None:
            now = datetime.now(timezone.utc)
        age = (now - ts).total_seconds()
        # Per-entry → per-action override → global default. The override layer
        # is what makes the fix backward-compatible with stored legacy entries.
        if "expiry_seconds" in entry:
            expiry = entry["expiry_seconds"]
        elif action_type and action_type in ACTION_EXPIRY_OVERRIDES:
            expiry = ACTION_EXPIRY_OVERRIDES[action_type]
        else:
            expiry = APPROVAL_EXPIRY_SECONDS
        return age < expiry
    except (KeyError, ValueError):
        return False


def check_destructive_approval(action_type: str) -> bool:
    """Check if there is a recent approval for the given action.

    Uses exact canonical key matching, plus superset lookup
    (e.g. force_push approval satisfies git_push check).
    """
    store = _load_approvals()
    approvals = store.get("approvals", {})
    if not approvals:
        _log("DEBUG", "No approvals in file")
        return False

    canonical = _normalize_action_type(action_type)
    now = datetime.now(timezone.utc)

    # Direct match — pass canonical so per-action override applies to legacy entries
    entry = approvals.get(canonical)
    if entry and _is_approval_valid(entry, now, action_type=canonical):
        _log("INFO", f"Approval valid (direct): {canonical}")
        return True

    # Superset match: check if any approved action covers this one
    for approved_key, approved_entry in approvals.items():
        if not _is_approval_valid(approved_entry, now, action_type=approved_key):
            continue
        covered = ACTION_SUPERSETS.get(approved_key, set())
        if canonical in covered:
            _log("INFO", f"Approval valid (superset): {approved_key} covers {canonical}")
            return True

    _log("DEBUG", f"No valid approval for '{canonical}' (have: {list(approvals.keys())})")
    return False


def grant_destructive_approval(action_type: str, expiry_seconds: int = None):
    """Grant approval for a destructive action (additive, preserves other approvals).

    Uses file locking to prevent TOCTOU race conditions.
    """
    canonical = _normalize_action_type(action_type)

    def _grant():
        store = _load_approvals()
        entry = {"timestamp": datetime.now(timezone.utc).isoformat()}
        # Persist the effective expiry on the entry so downstream readers
        # see a consistent value even if the override map is later edited.
        effective_expiry = expiry_seconds
        if effective_expiry is None and canonical in ACTION_EXPIRY_OVERRIDES:
            effective_expiry = ACTION_EXPIRY_OVERRIDES[canonical]
        if effective_expiry is not None:
            entry["expiry_seconds"] = effective_expiry
        store["approvals"][canonical] = entry
        _save_approvals(store)
        _log("INFO", f"Approval granted: {canonical}" + (f" (expiry={effective_expiry}s)" if effective_expiry else ""))

    _locked_approval_operation(_grant)


def clear_destructive_approval(action_type: str):
    """Remove ONE approval by canonical key (preserves other approvals).

    Uses file locking to prevent TOCTOU race conditions.
    """
    canonical = _normalize_action_type(action_type)

    def _clear():
        store = _load_approvals()
        if canonical in store.get("approvals", {}):
            del store["approvals"][canonical]
            _save_approvals(store)
            _log("INFO", f"Approval cleared: {canonical}")
        else:
            _log("DEBUG", f"No approval to clear for: {canonical}")

    _locked_approval_operation(_clear)


# =========================================================================
# ANSWER-GATED APPROVAL (#4809 Fix A)
# =========================================================================
# Origin: the PreToolUse path in hooks/context-gate-hook.py used to grant a
# destructive approval from the TEXT OF THE QUESTION, at the moment the
# question was asked — before any answer existed (old _gate_0_always_allow /
# _process_approval_question). An agent that merely *mentioned* "force push"
# in a question therefore held force_push approval for its full TTL whether
# the human clicked yes or no. The approval was answer-blind.
#
# The functions below split that into two honest halves:
#   1. actions_requested_by_question() — PURE. What is the question ASKING
#      permission for? Grants nothing, writes nothing.
#   2. classify_answer() + grant_from_answer() — the human's ANSWER decides.
#      Nothing is granted unless the answer is affirmatively consenting.
#
# Precision beats recall here, deliberately. A missed affirmative costs one
# repeated question; a false affirmative hands out a destructive approval the
# operator never gave. So the default verdict is AMBIGUOUS (grants nothing)
# and any negative marker overrides any affirmative one.

# Question-side keywords. Moved from hooks/context-gate-hook.py:795-798 so the
# ask-time reminder and the answer-time grant read one definition (DRY).
#
# The deploy and git-push words are matched on WORD BOUNDARIES, like the answer
# markers below (GRIP#7457). They used to be raw substring tests, so "produce",
# "product" and "reproduce" read as a deploy request, and "domain", "maintain",
# "digit" and "original" read as a push target; a plain "Yes" to such a question
# then granted a deploy or a push nobody had asked about.
#
# GRIP#7482 narrowed two more things. The past-tense forms ("pushed", "deployed")
# no longer count, because a question that reports finished work is not asking
# permission. And the broad force-push words ("restore", "discard", "destructive")
# count only as whole words in a question that also names git context, so "which
# gesture restores the item" and "a retain/discard gate" no longer ask to force-push.
# The precise force-push phrases still count on their own, as before.
#
# Every word below is an old keyword or an inflection of one with the same meaning,
# so a question can only lose a requested action (test_every_question_keyword_
# contains_an_old_keyword pins that). One that loses force_push may fall through to
# git_push, which force_push already covered (ACTION_SUPERSETS), so no question
# grants more than before.
DEPLOY_QUESTION_KEYWORDS = (
    "deploy", "deploys", "deploying", "deployment", "deployments",
    "redeploy", "redeploys", "redeploying", "redeployment",
    "production", "prod",
)
FORCE_PUSH_QUESTION_KEYWORDS = (
    "force push", "force-push", "--force", "reset --hard",
    "checkout --", "stash drop", "branch -d",
)
FORCE_PUSH_BROAD_KEYWORDS = ("restore", "discard", "destructive")
# Words that name git itself, not words that merely occur in git talk: "commit",
# "reset", "checkout", "head" and "staged" all have common non-git senses.
GIT_CONTEXT_KEYWORDS = (
    "git", "branch", "commits", "stash", "rebase", "worktree", "working tree",
    "uncommitted", "unstaged", "local changes",
)
GIT_PUSH_VERB_KEYWORDS = ("push", "pushes", "pushing")
# "github" is listed because the substring test used to find "git" inside it.
GIT_PUSH_QUESTION_KEYWORDS = ("git", "github", "origin", "main", "remote", "remotes")
PLAN_EXEC_QUESTION_KEYWORDS = (
    "execute plan", "execute preplan", "cached plan", "cached preplan",
    "run plan", "run preplan",
)

# Answer-side markers, matched on word boundaries against the lowered answer.
# NEGATIVE is evaluated first and wins outright: "do not force push" and
# "cancel the deploy" must never read as consent just because they also carry
# an affirmative-looking token.
#
# "skip", "wait", "later", "revert", "without" and "instead" stay in this
# anywhere-matched list on purpose, against a review that asked for them to be
# dropped or scoped to leading position. Scoping them was tried and MEASURED:
# over every AskUserQuestion answer in this machine's transcripts it changed the
# grant rate on approvable questions by exactly zero (6/12 either way — the whole
# gain comes from IMPERATIVE_LEAD_MARKERS below), while it broke a real safety
# property. multiSelect answers arrive comma-joined, so "Approve the merge, Skip
# the force push" would have granted force_push: the second SELECTED LABEL is
# textually indistinguishable from a subordinate clause like ", revert later".
# Refusing a qualified consent costs one repeated question; granting a
# force_push the operator declined costs the repository. Fail closed.
#
# Falsifier: a transcript answer to an approvable question that classifies
# NEGATIVE solely because one of these six appears mid-sentence. None exists in
# the 195-answer corpus today; one would reopen the question.
NEGATIVE_ANSWER_MARKERS = (
    "no", "nope", "not", "not now", "don't", "do not", "never", "cancel",
    "abort", "stop", "skip", "reject", "deny", "decline", "refuse", "dismiss",
    "hold off", "wait", "leave it", "revert", "undo", "negative", "disagree",
    "without", "instead", "later", "except", "excluding",
)
# "not"/"except"/"excluding" carry the carve-out class: an answer that consents
# to the safe part while EXCLUDING the destructive part in plain English —
# "Yes but not the force push", "Approve but not to production", "Deploy only,
# not the force push" — must not grant the excluded action. Because NEGATIVE is
# checked first and any match wins, the whole answer refuses and grants nothing;
# the operator re-confirms the part they wanted, which is the fail-safe
# direction. Word-boundary matching keeps these out of "notify"/"cannot"/etc.
# (test_a_carve_out_answer_grants_nothing pins the exploit AGORA missed).

# Deliberately narrow. Every entry is unambiguous consent language on its own;
# words that only mean yes in context ("continue", "go", "sure thing") are
# omitted so they land in AMBIGUOUS and grant nothing.
AFFIRMATIVE_ANSWER_MARKERS = (
    "yes", "yeah", "yep", "approve", "approved", "approval", "confirm",
    "confirmed", "proceed", "go ahead", "do it", "ok", "okay", "allow",
    "allowed", "grant", "granted", "accept", "accepted", "agree", "agreed",
    "authorise", "authorize", "authorised", "authorized", "permit",
    "affirmative", "green light", "ship it", "send it", "sure",
)

# Consent in the wild is rarely the word "yes". Measured over every
# AskUserQuestion answer in this machine's transcripts
# (~/.claude/projects/-Users-void--grip/*.jsonl): of the 12 answers to questions
# that request an approvable action, ZERO carried any marker above. They were
# instructions — "Deploy now", "Push + deploy live now", "Discard it",
# "Kill previews, keep production", "force push the rebase". The workflow was
# therefore disabled in practice: the operator consents, the downstream gate
# still denies, the agent re-asks.
#
# So an answer that OPENS with an imperative action verb reads as consent.
# Leading position is what keeps this precise — "Cancel the deploy" and
# "Do not push" still refuse, because the negative markers are checked first
# and match anywhere. The list stays narrow and destructive-action-shaped;
# generic verbs ("run", "check", "name") are omitted so they stay AMBIGUOUS.
#
# Residual risk, deliberately accepted: option labels are agent-authored, so an
# agent could word a DECLINE option as an imperative ("Discard it" offered as
# the refusal to a deploy question) and have it read as consent. The operator
# still sees and picks the exact text, and the question — not the answer —
# chooses WHICH action is on the table. Falsifier: any real transcript where a
# refusal label opens with one of these verbs; that would mean scoping the
# grant to a verb the question also names, not widening the list further.
# Corpus-grounded (195-answer transcript scan, 2026-08-18): six of these verbs
# — deploy, push, force push, discard, wipe, kill — actually lead a real
# affirmative answer; the rest are their unambiguous consent-siblings (a lead of
# "redeploy"/"ship"/"publish"/"release" means "do the deploy" exactly as
# "deploy" does; "force-push" is "force push"). Six verbs were CUT from the
# earlier list — merge, delete, remove, drop, overwrite, reset — because each
# plausibly LEADS a benign or refusing answer to a destructive question
# ("Remove the preview, don't deploy" answering "Deploy now?"), so keeping them
# widened the affirmative surface with zero evidence they are ever needed as a
# consent lead. This is the "cut to what the corpus supports" half of the fix.
#
# Verb-to-action COUPLING was considered and REJECTED: the corpus shows three of
# the six real grants are cross-verb — "Wipe it"/"Kill previews" granting deploy,
# "Discard it" granting force_push — because the operator answers with their own
# mental verb, not the internal action label. Coupling would have silently
# dropped half the genuine grants. The real second factor is
# actions_requested_by_question: an imperative lead grants ONLY the actions the
# QUESTION already put on the table, so a benign question grants nothing whatever
# the answer says. That invariant is pinned by tests, not asserted here.
#
# Falsifier (unchanged): any real transcript where a REFUSAL label opens with one
# of these verbs; that would mean narrowing further, never widening.
IMPERATIVE_LEAD_MARKERS = (
    "deploy", "redeploy", "push", "force push", "force-push",
    "ship", "publish", "release", "discard", "wipe", "kill",
)

AFFIRMATIVE = "affirmative"
NEGATIVE = "negative"
AMBIGUOUS = "ambiguous"


def _marker_present(text: str, markers) -> bool:
    """True when any marker appears in `text` on word boundaries.

    Word boundaries matter: a substring test would score "no" inside
    "notify", "now" and "another", turning most answers negative.
    """
    for marker in markers:
        if re.search(r"(?<!\w)" + re.escape(marker) + r"(?!\w)", text):
            return True
    return False


def _leads_with(text: str, markers) -> bool:
    """True when `text` OPENS with one of `markers`, ignoring leading punctuation.

    The leading `\\W*` lets "(deploy now)" and "-- push it" match; the trailing
    boundary stops "pushed back" scoring as "push".
    """
    for marker in markers:
        if re.match(r"\W*" + re.escape(marker) + r"(?!\w)", text):
            return True
    return False


def classify_answer(answer) -> str:
    """Classify a human's AskUserQuestion answer as consent, refusal, or neither.

    `answer` is whatever the harness put in tool_response["answers"][question].
    Verified shape (transcript toolUseResult, e.g.
    projects/-Users-void--grip/5d9e47a9-*.jsonl): a string carrying the label of
    the selected option, or — for multiSelect — the selected labels joined with
    ", ", or the operator's free text when they used the Other box.

    Returns AFFIRMATIVE, NEGATIVE or AMBIGUOUS. Anything that is not a
    non-empty string is AMBIGUOUS, so a missing or malformed answer grants
    nothing.
    """
    if isinstance(answer, (list, tuple)):
        # Defensive: if the harness ever hands back a list of labels rather
        # than a joined string, a single refusal among them still refuses.
        parts = [str(a) for a in answer]
        verdicts = [classify_answer(p) for p in parts]
        if NEGATIVE in verdicts:
            return NEGATIVE
        return AFFIRMATIVE if AFFIRMATIVE in verdicts else AMBIGUOUS
    if not isinstance(answer, str) or not answer.strip():
        return AMBIGUOUS
    text = answer.lower()
    if _marker_present(text, NEGATIVE_ANSWER_MARKERS):
        return NEGATIVE
    if _marker_present(text, AFFIRMATIVE_ANSWER_MARKERS) or _leads_with(text, IMPERATIVE_LEAD_MARKERS):
        return AFFIRMATIVE
    return AMBIGUOUS


def _requests_force_push(text: str) -> bool:
    """True when the lowered question asks for a destructive git action.

    A precise phrase ("force push", "stash drop") counts on its own. A broad word
    ("restore", "discard", "destructive") counts only as a whole word in a question
    that also names git context (GRIP#7482).
    """
    if any(kw in text for kw in FORCE_PUSH_QUESTION_KEYWORDS):
        return True
    return _marker_present(text, FORCE_PUSH_BROAD_KEYWORDS) and _marker_present(text, GIT_CONTEXT_KEYWORDS)


def actions_requested_by_question(question_text: str) -> list[tuple[str, int]]:
    """What destructive actions is this question ASKING permission for?

    PURE — grants nothing, writes nothing. Returns (canonical_action, expiry)
    pairs; expiry is None when the action's default TTL applies.

    Match semantics came over from the old _check_deploy_approval/
    _check_git_approval/_check_code_write_approval/_check_ssh_approval chain,
    with one deliberate narrowing (GRIP#7457): the deploy and git-push words
    now match on word boundaries instead of as substrings, so a question about
    what a tool should "produce" no longer asks to deploy. GRIP#7482 added a
    second: past-tense forms no longer count, and the broad force-push words
    need git context (see the keyword lists). Neither narrowing can make a
    question grant more than before. Missing a real request costs one repeated
    question; a false request let a plain "Yes" grant an action nobody had
    asked about. The precise force-push phrases and the code-write and ssh
    tests are unchanged substring matches.
    """
    if not isinstance(question_text, str):
        return []
    text = question_text.lower()
    requested: list[tuple[str, int]] = []

    if _marker_present(text, DEPLOY_QUESTION_KEYWORDS):
        requested.append((ACTION_DEPLOY, None))

    # force_push is checked before git_push and short-circuits it, mirroring
    # the original elif chain. ACTION_SUPERSETS already makes force_push cover
    # git_push, so nothing is lost.
    if _requests_force_push(text):
        requested.append((ACTION_FORCE_PUSH, None))
    elif _marker_present(text, GIT_PUSH_VERB_KEYWORDS) and _marker_present(text, GIT_PUSH_QUESTION_KEYWORDS):
        requested.append((ACTION_GIT_PUSH, None))

    if any(kw in text for kw in CODE_WRITE_APPROVAL_KEYWORDS):
        is_batch = any(kw in text for kw in CODE_WRITE_BATCH_KEYWORDS)
        requested.append((ACTION_CODE_WRITE, 900 if is_batch else None))

    if "tailscale ssh" in text or "vps deploy" in text or "ssh to" in text:
        requested.append((ACTION_TAILSCALE_SSH, None))

    return requested


def question_requests_plan_execution(question_text: str) -> bool:
    """True when the question asks to execute a cached plan (Gate P marker)."""
    if not isinstance(question_text, str):
        return False
    text = question_text.lower()
    return any(kw in text for kw in PLAN_EXEC_QUESTION_KEYWORDS)


def grant_from_answer(question_text: str, answer) -> list[str]:
    """Grant approvals for one answered question. Returns granted action keys.

    Default DENY: an ambiguous, negative, missing or malformed answer grants
    nothing at all. This is the whole point of the fix — the question alone
    can never confer a privilege.
    """
    requested = actions_requested_by_question(question_text)
    if not requested:
        return []
    if classify_answer(answer) != AFFIRMATIVE:
        _log(
            "INFO",
            "Approval withheld — answer is not affirmative for: "
            + ", ".join(action for action, _ in requested),
        )
        return []
    granted = []
    for action, expiry in requested:
        grant_destructive_approval(action, expiry_seconds=expiry)
        granted.append(action)
    return granted


def mark_plan_executing(plan_cache_file: Path) -> bool:
    """Flip a cached plan to status=executing so Gate P stops re-firing.

    Moved out of hooks/context-gate-hook.py._mark_plan_executing so it too runs
    from the answer rather than the question. Returns True when the file was
    updated. Never raises: a missing or corrupt plan cache is not a reason to
    fail an approval pass.
    """
    if not plan_cache_file.exists():
        return False
    # Lock the read-modify-write: grant_destructive_approval already serialises
    # its own file (_locked_approval_operation); this write to the plan cache is
    # the one grant-path mutation that was left unlocked, so two near-simultaneous
    # approvals could interleave and lose the status flip. The lock file is
    # separate from the data file, mirroring the approval lock.
    lock_path = plan_cache_file.parent / (plan_cache_file.name + ".lock")
    try:
        with FileLock(str(lock_path), timeout=5):
            try:
                data = json.loads(plan_cache_file.read_text())
            except (json.JSONDecodeError, OSError) as e:
                _log("ERROR", f"Plan cache unreadable, not marking executing: {e}")
                return False
            if not isinstance(data, dict):
                return False
            data["status"] = "executing"
            try:
                plan_cache_file.write_text(json.dumps(data))
            except OSError as e:
                _log("ERROR", f"Failed to mark plan executing: {e}")
                return False
    except LockTimeoutError:
        # A concurrent writer holds the lock; withholding the flip is fail-safe
        # (Gate P will re-fire and the operator re-confirms) — never raise.
        _log("ERROR", "Plan-cache lock busy, not marking executing this pass")
        return False
    _log("INFO", "Plan status -> executing (user approved)")
    return True
