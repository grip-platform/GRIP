#!/usr/bin/env python3
"""Task-reality reconciliation — referent extraction + fail-open probing (W1).

Solves task-list drift: the append-only-ledger-consumed-as-state-database bug.
A harness task's ``status`` field is SELF-REPORT (telemetry), never criterion.
The criterion is the *exogenous referent* — a URL that returns 200, a PR that is
merged, a file that exists, a repo that resolves. This module extracts the
machine-checkable referents from a task's free text and probes their factual
existence/state, so a reader can fence stale status at the point of consumption.

Doctrine: ``exogenous-anchor-law.md`` / ``verify-canonical-not-claimed.md``
applied to task state. A task is done **iff reality satisfies its done-predicate**,
not iff its status says so.

fail-open by construction: ANY probe error/timeout/ambiguity ⇒ ``ok=False`` with
state ``'unknown'``, so the W2 classifier degrades that task to UNVERIFIABLE and
can NEVER emit a false "done". A missed referent is safe (UNVERIFIABLE); a wrong
confident fact is not — so extraction favours precision over recall.

W1 scope (this wave): ``extract_referents`` (url/pr/path/repo via one
priority-ordered alternation regex — ``finditer`` yields non-overlapping
left-to-right matches, so alternation order *is* the confidence priority and no
manual overlap resolution is needed) + ``probe_referent`` returning factual
``ProbeFact``s, DIP-injected via ``Probes`` so tests are hermetic. The 3-way
verdict classifier ({PROVABLY-DONE, PROVABLY-OPEN, UNVERIFIABLE-STALE}) is W2 and
consumes these facts.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import List, Optional, Tuple

__all__ = [
    "Referent",
    "ProbeFact",
    "Probes",
    "DEFAULT_PROBES",
    "extract_referents",
    "probe_referent",
    "probe_task",
    # W2 — classifier + report
    "Verdict",
    "TaskVerdict",
    "ReconcileReport",
    "classify_verb",
    "classify_task",
    "reconcile_tasks",
    "render_report",
]


# --------------------------------------------------------------------------- #
# Referent model
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Referent:
    """A machine-checkable claim extracted from a task's text.

    kind  — one of 'url' | 'pr' | 'issue' | 'path' | 'repo' | 'named_pr'.
    value — the canonical token to probe (url, PR or issue number or
            'owner/repo#n', path, 'owner/repo', or — for 'named_pr' only —
            the raw '<Name>#<N>' shorthand, unresolved until probe time).
    raw   — the exact matched substring (provenance for the surfaced evidence).
    """

    kind: str
    value: str
    raw: str = ""


@dataclass(frozen=True)
class ProbeFact:
    """The factual result of probing a referent — existence/state only, NOT a
    done/open verdict (that is W2's job, which weighs the task verb too).

    state — 'up'|'down' (url) | 'merged'|'open'|'closed' (pr) |
            'open'|'closed' (issue) | 'exists'|'absent' (path/repo) |
            'unknown' (probe failed/ambiguous).
    ok    — True iff the probe yielded a trustworthy fact. ``ok=False`` is the
            fail-open signal: never let an unreliable probe drive a "done".
    """

    referent: Referent
    state: str
    detail: str = ""
    ok: bool = True


# --------------------------------------------------------------------------- #
# Extraction — ONE priority-ordered alternation regex. ``finditer`` returns
# non-overlapping, left-to-right matches and alternation tries url→pr→path→repo
# at each position, so the highest-confidence kind wins automatically (a github
# URL is one 'url', never additionally a 'repo'). Single pass, O(n) in text len.
# --------------------------------------------------------------------------- #
_CODE_DIRS = frozenset(
    "lib tests skills hooks scripts rules bin docs config apps web data state "
    "mcp-servers".split()
)

# Git branch prefixes — 'feat/example-mcp-scaffold' is a branch ref, not an
# owner/repo. Dropped from repo extraction (a branch is not a probeable repo).
# Narrowed (AGORA #3370 Speaker C) to the UNAMBIGUOUS git-flow prefixes only:
# 'ci', 'build', 'test', 'docs', 'perf', 'style', 'refactor' are common words
# that can be real repo owners, so they are NOT excluded — the slash-aware verb
# boundary below already stops 'build' in 'ci/build' from false-triggering.
_BRANCH_PREFIXES = frozenset("feat fix chore hotfix bugfix release revert".split())

_TOKEN_RE = re.compile(
    r"(?P<url>https?://[^\s<>\"')\]}]+)"
    # A gh command that names a PR or an issue by number -- 'gh pr merge 9069
    # --squash --repo grip-platform/GRIP' -- taken whole, through an optional
    # --repo/-R on the same command line, so the number becomes a probeable
    # referent and the repo is not taken a second time as a plain 'repo'
    # (whose existence says nothing about the PR). The --repo search stops at
    # a line end, a shell separator, a backtick or the next 'gh', so one
    # command never takes another's repo. A command whose number comes after
    # its flags is not matched: precision first, as everywhere.
    r"|(?P<gh_cmd>(?<![\w/.-])gh\s+(?:pr|issue)\s+[a-z][a-z-]*\s+#?\d{1,7}\b"
    r"(?:(?:(?!\bgh\s)[^\n|;&`])*?\s(?:--repo|-R)[=\s]+[A-Za-z0-9][\w.-]*/[A-Za-z0-9][\w.-]*)?)"
    r"|(?P<pr>(?:[A-Za-z0-9][\w.-]*/[A-Za-z0-9][\w.-]*#|PR\s*#|(?<![\w])#)\d{1,7}\b)"
    # Repo-qualified shorthand with NO slash — 'GRIP#9069', 'HAL#120'. The
    # existing `pr` alternative above requires either a full 'owner/repo#',
    # literal 'PR #', or a bare '#' with no preceding word char, so none of
    # its three branches match this shape; tried only once those have failed
    # at this position, so 'grip-platform/GRIP#2723' is still consumed whole
    # by the `pr` alternative starting at its own 'C', never re-matched here.
    # Resolved against the current git remote at PROBE time (_probe_named_pr)
    # — extraction stays pure/no-I/O, exactly like every other branch here.
    r"|(?P<named_pr>(?<![\w/.-])[A-Za-z][\w-]*#\d{1,7}\b)"
    r"|(?P<path>(?:/[\w.-]+)+\.\w{1,8}\b"
    r"|(?:lib|tests|skills|hooks|scripts|rules|bin|docs|config|apps|web|data|state|"
    r"mcp-servers)/[\w./-]+\.\w{1,8}\b"
    # An executable under bin/ or scripts/ often has no extension
    # ('bin/grip-continue-card'): one segment, ending on a word character.
    r"|(?<![\w/.-])(?:bin|scripts)/[A-Za-z0-9](?:[\w.-]*[\w-])?(?![\w/-]))"
    # Bare domain (no scheme) — the real failure-class text (#1648 names a
    # bare domain, not one written with https://). TLD-allowlisted so file names
    # (.py/.ts/.json/.md) and version tokens (v.01) can NEVER match.
    r"|(?P<domain>(?<![\w/@.-])(?:[A-Za-z0-9][\w-]*\.)+"
    r"(?:com|org|io|net|dev|pro|ai|app|co|xyz|sh|me|tech|cloud|page|gg)\b"
    r"(?:/[^\s<>\"')\]}]*)?)"
    # owner/repo — exactly ONE slash. The boundary lookbehind + no-trailing-slash
    # lookahead reject a 2-segment slice of a longer noun-list chain
    # ('deploy/publish/cid/list' is subcommands, not a repo).
    r"|(?P<repo>(?<![/\w.-])[A-Za-z0-9][\w.-]*/[A-Za-z0-9][\w.-]*(?![\w/-]))"
)


# Claude Code's own placeholders for pasted content, "[Image #12]" and
# "[Pasted text #3 +40 lines]". Their numbers count pastes in the session, not
# PRs: a message with three screenshots put "#12 PR MERGED" on the /continue
# card for an unrelated GRIP#12 (GRIP#9433). Blanked before the scan. Only the
# exact forms Claude Code writes are blanked, so a number a person types inside
# other brackets still counts (AGORA on GRIP#9787).
_PLACEHOLDER_RE = re.compile(r"\[(?:Image #\d+|Pasted text #\d+(?: \+\d+ lines?)?)\]")


def _pr_value_from_raw(raw: str) -> str:
    """'grip-platform/GRIP#2723' -> 'grip-platform/GRIP#2723'; 'PR #3365' and
    '#3365' -> '3365' (bare number, repo resolved from cwd at probe time)."""
    before, _, num = raw.rpartition("#")
    before = before.strip()
    return f"{before}#{num}" if "/" in before else num


_GH_CMD_HEAD_RE = re.compile(r"gh\s+(pr|issue)\s+[a-z][a-z-]*\s+#?(\d{1,7})")
_GH_REPO_FLAG_RE = re.compile(r"\s(?:--repo|-R)[=\s]+([A-Za-z0-9][\w.-]*/[A-Za-z0-9][\w.-]*)")


def _gh_cmd_referent(raw: str) -> Optional[Referent]:
    """'gh pr merge 9069 --repo O/R' -> Referent('pr', 'O/R#9069'). With no
    --repo, the bare number: gh resolves it against the current checkout's
    remote at probe time, exactly as the command itself would."""
    head = _GH_CMD_HEAD_RE.match(raw)
    if head is None:
        return None
    kind, num = head.groups()
    repo = _GH_REPO_FLAG_RE.search(raw)
    return Referent(kind, f"{repo.group(1)}#{num}" if repo else num, raw)


def _build_referent(kind: str, raw: str) -> Optional[Referent]:
    """Map one regex match to a Referent (or None to drop a code-dir 'repo')."""
    if kind == "url":
        return Referent("url", raw.rstrip(".,;:)"), raw)
    if kind == "gh_cmd":
        return _gh_cmd_referent(raw)
    if kind == "domain":  # bare host → probe as a url with an https scheme
        return Referent("url", "https://" + raw.rstrip(".,;:)"), raw)
    if kind == "pr":
        return Referent("pr", _pr_value_from_raw(raw), raw)
    if kind == "named_pr":
        return Referent("named_pr", raw, raw)
    if kind == "path":
        return Referent("path", raw, raw)
    if kind == "repo":
        owner = raw.split("/", 1)[0]
        if owner in _CODE_DIRS or owner in _BRANCH_PREFIXES:  # path/ or branch/, not a repo
            return None
        return Referent("repo", raw, raw)
    return None


def extract_referents(text: str) -> List[Referent]:
    """Extract machine-checkable referents from free task text.

    Pure, deterministic, single-pass, no I/O. Precision-first: a kind that cannot
    be confidently identified is simply not extracted (a missed referent yields
    UNVERIFIABLE downstream, which is safe; a wrong one is not).
    """
    if not text:
        return []
    out: List[Referent] = []
    for m in _TOKEN_RE.finditer(_PLACEHOLDER_RE.sub(" ", text)):
        ref = _build_referent(m.lastgroup or "", m.group(0))
        if ref is not None:
            out.append(ref)
    return out


# --------------------------------------------------------------------------- #
# Probe backends — the DIP seams. Defaults hit the real world; tests inject fakes.
# --------------------------------------------------------------------------- #
def _default_http_get(url: str, timeout: float = 6.0) -> int:
    """GET ``url`` (following redirects); return the HTTP status code. A 4xx/5xx
    is returned as a code (caught from HTTPError) — only a genuine transport
    failure (DNS, timeout, refused) raises, which the caller fail-opens on."""
    req = urllib.request.Request(
        url, method="GET", headers={"User-Agent": "grip-task-reconcile/1"}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return int(resp.status)
    except urllib.error.HTTPError as exc:
        return int(exc.code)


def _default_gh(args: List[str], timeout: float = 15.0,
                cwd: Optional[str] = None) -> Tuple[int, str, str]:
    """Run ``gh <args>`` in ``cwd`` (the process's own when None); return
    (returncode, stdout, stderr), all stripped. The folder matters: with no
    --repo, gh resolves "the current repository" from it."""
    proc = subprocess.run(
        ["gh", *args], capture_output=True, text=True, timeout=timeout, cwd=cwd
    )
    return proc.returncode, proc.stdout.strip(), proc.stderr.strip()


class Probes:
    """Bundle of injected probe backends (DIP seam for hermetic tests).

    Plain class (not a dataclass) so the callable attributes are ordinary
    instance attributes — ``probes.http_get(url)`` invokes the function with no
    implicit ``self`` binding. Tests inject any subset; unset ⇒ real default.
    """

    __slots__ = ("http_get", "gh", "path_exists", "root")

    def __init__(self, http_get=None, gh=None, path_exists=None, root=""):
        self.http_get = http_get or _default_http_get
        # The default gh runs in `root`, so a bare '#N' or 'GRIP#N' resolves
        # against the tree the caller asked about, not the process's own cwd.
        self.gh = gh or (lambda args: _default_gh(args, cwd=root or None))
        self.path_exists = path_exists or os.path.exists
        self.root = root  # repo root for relative paths; "" ⇒ os.getcwd()


DEFAULT_PROBES = Probes()


def _probe_url(ref: Referent, probes: Probes) -> ProbeFact:
    code = int(probes.http_get(ref.value))
    if 200 <= code < 400:
        return ProbeFact(ref, "up", f"HTTP {code}")
    if code >= 500:  # server error — transient/ambiguous, fail-open
        return ProbeFact(ref, "unknown", f"HTTP {code}", ok=False)
    return ProbeFact(ref, "down", f"HTTP {code}")  # 4xx — genuinely not serving


_PR_STATE_MAP = {"MERGED": "merged", "OPEN": "open", "CLOSED": "closed"}
_ISSUE_STATE_MAP = {"OPEN": "open", "CLOSED": "closed"}


def _looks_like_pr_not_found(err: str) -> bool:
    """True for gh's own wording when a number names no PR — never for an
    auth/network/deprecation failure, which must stay a plain fail-open."""
    low = err.lower()
    return "pull" in low and ("could not resolve" in low or "not found" in low)


def _gh_issue_state(num: str, repo: str, probes: Probes) -> Tuple[int, str, str]:
    """`gh issue view <N>`, with the same 15s default timeout and the same
    fail-open seam (`probes.gh`) as the PR path."""
    args = ["issue", "view", num, "--json", "state", "-q", ".state"]
    if repo:
        args += ["--repo", repo]
    return probes.gh(args)


def _issue_fact(ref: Referent, num: str, repo: str, out: str) -> ProbeFact:
    """An issue's state on an 'issue' referent, so no reader is told an issue
    is a PR. `raw` keeps the text the referent was found as ('GRIP#9067')."""
    state = out.strip().upper()
    issue = Referent("issue", f"{repo}#{num}" if repo else num, ref.raw)
    return ProbeFact(
        issue, _ISSUE_STATE_MAP.get(state, "unknown"), f"gh issue: {state}",
        ok=state in _ISSUE_STATE_MAP,
    )


def _probe_issue(ref: Referent, probes: Probes) -> ProbeFact:
    repo, _, num = ref.value.rpartition("#")
    rc, out, err = _gh_issue_state(num, repo, probes)
    if rc != 0:  # fail-open, exactly as the PR path
        return ProbeFact(ref, "unknown", f"gh rc={rc}: {err[:80]}", ok=False)
    return _issue_fact(ref, num, repo, out)


def _probe_pr(ref: Referent, probes: Probes) -> ProbeFact:
    repo, _, num = ref.value.rpartition("#")
    args = ["pr", "view", num, "--json", "state", "-q", ".state"]
    if repo:
        args += ["--repo", repo]
    rc, out, err = probes.gh(args)
    if rc != 0:  # fail-open: could be deprecation/auth/network, not a verdict
        if _looks_like_pr_not_found(err):
            # The number names no PR: ask whether it names an issue. A failed
            # issue lookup is never a confident state -- the plain unknown below.
            irc, iout, _ierr = _gh_issue_state(num, repo, probes)
            if irc == 0:
                return _issue_fact(ref, num, repo, iout)
        return ProbeFact(ref, "unknown", f"gh rc={rc}: {err[:80]}", ok=False)
    state = out.strip().upper()
    return ProbeFact(
        ref, _PR_STATE_MAP.get(state, "unknown"), f"gh: {state}",
        ok=state in _PR_STATE_MAP,
    )


def _probe_named_pr(ref: Referent, probes: Probes) -> ProbeFact:
    """Resolve a bare '<Name>#<N>' shorthand (e.g. 'GRIP#9069') against the
    CURRENT git remote's repo name, case-insensitively, then delegate to
    ``_probe_pr``. Never guesses an owner: a name that does not match the
    current remote, or a remote that cannot be read at all, fails open as
    unknown rather than probing a guessed repository."""
    name, _, num = ref.value.rpartition("#")
    rc, out, err = probes.gh(["repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner"])
    if rc != 0 or not out.strip():
        return ProbeFact(
            ref, "unknown", f"could not read the current repo: gh rc={rc}: {err[:80]}", ok=False,
        )
    owner_repo = out.strip()
    current_name = owner_repo.rpartition("/")[-1]
    if current_name.lower() != name.lower():
        return ProbeFact(
            ref, "unknown",
            f"{name!r} does not match the current remote {owner_repo!r} -- not guessing an owner",
            ok=False,
        )
    resolved = Referent("pr", f"{owner_repo}#{num}", ref.raw)
    return _probe_pr(resolved, probes)


def _probe_path(ref: Referent, probes: Probes) -> ProbeFact:
    p = ref.value
    target = p if os.path.isabs(p) else os.path.join(probes.root or os.getcwd(), p)
    found = bool(probes.path_exists(target))
    return ProbeFact(
        ref, "exists" if found else "absent",
        f"{'found' if found else 'missing'}: {p}",
    )


def _probe_repo(ref: Referent, probes: Probes) -> ProbeFact:
    rc, out, err = probes.gh(["repo", "view", ref.value, "--json", "name", "-q", ".name"])
    if rc == 0 and out.strip():
        return ProbeFact(ref, "exists", f"gh repo: {out.strip()}")
    # rc != 0: genuinely-absent vs auth/network failure. Only a clear not-found
    # string is a trustworthy ABSENT; anything else fail-opens.
    low = err.lower()
    if "could not resolve" in low or "not found" in low:
        return ProbeFact(ref, "absent", "gh: repository not found")
    return ProbeFact(ref, "unknown", f"gh rc={rc}: {err[:80]}", ok=False)


_PROBE_DISPATCH: dict = {
    "url": _probe_url,
    "pr": _probe_pr,
    "issue": _probe_issue,
    "named_pr": _probe_named_pr,
    "path": _probe_path,
    "repo": _probe_repo,
}


def probe_referent(ref: Referent, probes: Probes = DEFAULT_PROBES) -> ProbeFact:
    """Probe one referent's factual existence/state. fail-open: ANY exception in
    a backend ⇒ ``ProbeFact(state='unknown', ok=False)`` — a probe error is never
    a verdict, so the upstream classifier degrades to UNVERIFIABLE and never
    false-closes a genuinely-open task."""
    fn = _PROBE_DISPATCH.get(ref.kind)
    if fn is None:
        return ProbeFact(ref, "unknown", f"no prober for kind {ref.kind!r}", ok=False)
    try:
        return fn(ref, probes)
    except Exception as exc:  # noqa: BLE001 — fail-open is the whole point
        return ProbeFact(ref, "unknown", f"{type(exc).__name__}: {exc}", ok=False)


def probe_task(text: str, probes: Probes = DEFAULT_PROBES) -> List[ProbeFact]:
    """W1 end-to-end for a single task: extract referents, probe each. Returns
    the ProbeFacts (empty when no referent is found — a no-referent task is
    inherently UNVERIFIABLE, classified as such in W2)."""
    return [probe_referent(r, probes) for r in extract_referents(text)]


# =========================================================================== #
# W2 — 3-way classifier + report
# =========================================================================== #
# A task is done IFF reality satisfies its done-predicate, and the predicate
# depends on the task VERB: ship/create/merge ⇒ existence-or-merge IS done;
# fix/refactor/investigate ⇒ existence is NOT done (the council's coverage cap —
# most real tasks fall here, so the classifier stays conservative and the L4
# behavioural gate, not this prober, is the headline). Unreliable probe facts
# (ok=False) never drive a verdict, so a probe error can never false-close.


class Verdict:
    """The three reconciliation outcomes (string constants, stable for storage)."""

    DONE = "PROVABLY-DONE"
    OPEN = "PROVABLY-OPEN"
    UNVERIFIABLE = "UNVERIFIABLE-STALE"


@dataclass(frozen=True)
class TaskVerdict:
    """A reconciled task: the exogenous verdict + the evidence that produced it."""

    task_id: str
    verdict: str
    evidence: str
    facts: Tuple[ProbeFact, ...] = ()


_VERB_MERGE = ("merge", "land")
_VERB_CLOSE = ("close",)
_VERB_EXISTENCE = (
    "ship", "publish", "launch", "deploy", "create", "set up", "setup",
    "build", "scaffold", "render", "generate", "stand up", "spin up",
)
_CONFIRM_STATES = frozenset({"up", "exists", "merged"})
_CONTRADICT_STATES = frozenset({"down", "absent"})


def _has_verb(low_text: str, verbs: Tuple[str, ...]) -> bool:
    # (?<![\w/-]) ... (?![\w/-]) instead of \b so a verb embedded in a hyphenated
    # compound OR a slash-path segment does NOT match — 'scaffold' inside
    # 'example-mcp-scaffold' (a branch name) and 'build' inside 'ci/build' / 'deploy'
    # inside 'deploy/publish' (slash-noun lists) are not the task's action verb.
    # Multi-word verbs ('set up') still match — the boundary is only checked at the
    # phrase ends, not the internal space. (\b already rejected '_'-adjacency, which
    # this preserves, so there is no apostrophe/underscore regression — AGORA #3370.)
    return any(re.search(rf"(?<![\w/-]){re.escape(v)}(?![\w/-])", low_text) for v in verbs)


def classify_verb(text: str) -> str:
    """Map a task's text to 'merge' | 'close' | 'existence' | 'other' by verb
    intent. 'merge' wins precedence (the deliverable IS the merge of a named
    PR), then 'close' ('gh issue close 9067': the deliverable is the closing)."""
    low = text.lower()
    if _has_verb(low, _VERB_MERGE):
        return "merge"
    if _has_verb(low, _VERB_CLOSE):
        return "close"
    if _has_verb(low, _VERB_EXISTENCE):
        return "existence"
    return "other"


def _evidence(label: str, facts: List[ProbeFact]) -> str:
    bits = "; ".join(f"{f.referent.value}={f.detail}" for f in facts[:3])
    return f"{label}: {bits}" if bits else label


def _verdict_merge(task_id: str, reliable: List[ProbeFact],
                   facts: Tuple[ProbeFact, ...]) -> "TaskVerdict":
    prs = [f for f in reliable if f.referent.kind == "pr"]
    if any(f.state == "merged" for f in prs):
        return TaskVerdict(task_id, Verdict.DONE, _evidence("merged", prs), facts)
    if any(f.state in ("open", "closed") for f in prs):
        return TaskVerdict(task_id, Verdict.OPEN, _evidence("not merged", prs), facts)
    return TaskVerdict(task_id, Verdict.UNVERIFIABLE, "merge verb, no PR state", facts)


def _is_path_only_absent(f: ProbeFact) -> bool:
    """A path probed absent is NOT a confident contradiction: the file may live in
    another repo (cross-repo task text probed from this cwd), so it degrades to
    UNVERIFIABLE. url 'down' and repo 'absent' (gh not-found) stay confident."""
    return f.state == "absent" and f.referent.kind == "path"


def _verdict_existence(task_id: str, reliable: List[ProbeFact],
                       facts: Tuple[ProbeFact, ...]) -> "TaskVerdict":
    confirm = [f for f in reliable if f.state in _CONFIRM_STATES]
    if confirm:
        return TaskVerdict(task_id, Verdict.DONE, _evidence("exists/up", confirm), facts)
    contradict = [
        f for f in reliable
        if f.state in _CONTRADICT_STATES and not _is_path_only_absent(f)
    ]
    if contradict:
        return TaskVerdict(task_id, Verdict.OPEN, _evidence("absent/down", contradict), facts)
    return TaskVerdict(task_id, Verdict.UNVERIFIABLE, "existence verb, ambiguous facts", facts)


def _verdict_other(task_id: str, reliable: List[ProbeFact],
                   facts: Tuple[ProbeFact, ...]) -> "TaskVerdict":
    # Non-existence verb (fix/investigate/harden/decide/...): a referent existing
    # does NOT prove the work done. Honest cap — L4 (the behavioural gate) carries
    # the load. Still surface any reliable referent facts so the human verifying
    # the UNVERIFIABLE task (per L4) sees the evidence (e.g. a domain that answers HTTP 200).
    base = "verb not existence/merge — referent existence != done"
    note = _evidence(base + " (verify by hand)", reliable) if reliable else base
    return TaskVerdict(task_id, Verdict.UNVERIFIABLE, note, facts)


def _verdict_close(task_id: str, reliable: List[ProbeFact],
                   facts: Tuple[ProbeFact, ...]) -> "TaskVerdict":
    """A close step is done when what it names is closed: an issue or PR in
    state 'closed' (a merged PR is closed too). One still open says not yet."""
    targets = [f for f in reliable if f.referent.kind in ("issue", "pr")]
    if any(f.state == "open" for f in targets):
        return TaskVerdict(task_id, Verdict.OPEN, _evidence("still open", targets), facts)
    if any(f.state in ("closed", "merged") for f in targets):
        return TaskVerdict(task_id, Verdict.DONE, _evidence("closed", targets), facts)
    return TaskVerdict(task_id, Verdict.UNVERIFIABLE, "close verb, no issue or PR state", facts)


_VERDICT_HANDLERS = {
    "merge": _verdict_merge,
    "close": _verdict_close,
    "existence": _verdict_existence,
    "other": _verdict_other,
}


def classify_task(task_id: str, text: str, facts) -> TaskVerdict:
    """Classify one task as PROVABLY-DONE / PROVABLY-OPEN / UNVERIFIABLE-STALE by
    weighing verb intent against its reliable probe facts. Conservative: only a
    verb→fact match yields DONE/OPEN; an all-unreliable fact set (every probe
    failed) is always UNVERIFIABLE — so this never false-closes a live task."""
    facts = tuple(facts)
    reliable = [f for f in facts if f.ok]
    if not reliable:
        return TaskVerdict(task_id, Verdict.UNVERIFIABLE, "no trustworthy referent", facts)
    return _VERDICT_HANDLERS[classify_verb(text)](task_id, reliable, facts)


@dataclass(frozen=True)
class ReconcileReport:
    """The reconciliation of a task set — verdicts + convenience views."""

    verdicts: Tuple[TaskVerdict, ...]

    def _by(self, v: str) -> List[TaskVerdict]:
        return [tv for tv in self.verdicts if tv.verdict == v]

    @property
    def done(self) -> List[TaskVerdict]:
        return self._by(Verdict.DONE)

    @property
    def open(self) -> List[TaskVerdict]:
        return self._by(Verdict.OPEN)

    @property
    def unverifiable(self) -> List[TaskVerdict]:
        return self._by(Verdict.UNVERIFIABLE)

    def summary(self) -> str:
        return (
            f"{len(self.done)} provably-done · {len(self.open)} provably-open · "
            f"{len(self.unverifiable)} unverifiable / {len(self.verdicts)} tasks"
        )


def _reconcile_one(task, probes: Probes) -> TaskVerdict:
    tid, text = (task["id"], task["text"]) if isinstance(task, dict) else (task[0], task[1])
    return classify_task(str(tid), text or "", probe_task(text or "", probes))


def reconcile_tasks(tasks, probes: Probes = DEFAULT_PROBES) -> ReconcileReport:
    """Reconcile an iterable of tasks against reality. Each task is a mapping with
    'id'/'text' (or an (id, text) pair). Extracts + probes each task's referents,
    classifies, returns a ReconcileReport. The probe set is injected so callers
    cache it (W3 session-start) rather than probing per-read."""
    return ReconcileReport(tuple(_reconcile_one(t, probes) for t in tasks))


# --------------------------------------------------------------------------- #
# Rendering + CLI — fence staleness at the point of consumption (L3). A reader
# sees ⚠ on provably-done items so the verify reflex fires on sight, and an
# honest UNVERIFIABLE count so the prober is never oversold.
# --------------------------------------------------------------------------- #
def render_report(report: ReconcileReport) -> str:
    """Plain-language reconciliation report. ⚠ flags provably-done tasks (close
    them); · marks confirmed-open and the honest unverifiable remainder."""
    lines = [f"Task reality check — {report.summary()}", ""]
    if report.done:
        lines.append("⚠ PROVABLY-DONE (reality says shipped — safe to close):")
        lines += [f"  #{tv.task_id} — {tv.evidence}" for tv in report.done]
        lines.append("")
    if report.open:
        lines.append("· PROVABLY-OPEN (reality confirms still open):")
        lines += [f"  #{tv.task_id} — {tv.evidence}" for tv in report.open]
        lines.append("")
    if report.unverifiable:
        lines.append(
            f"· UNVERIFIABLE-STALE ({len(report.unverifiable)}) — no machine-checkable "
            "referent; verify by hand before driving any plan/agent/doc off these."
        )
    return "\n".join(lines).rstrip() + "\n"


def main(argv: Optional[List[str]] = None) -> int:
    """CLI: reconcile a JSON task list (``[{"id":..,"text":..}, ...]``) from a
    file argument or stdin, printing the rendered report. Used by the
    ``task-reconcile`` skill and the W3 session-start surface."""
    import json

    argv = list(sys.argv[1:] if argv is None else argv)
    src = argv[0] if argv else "-"
    try:  # fail clean on bad input — never a raw traceback
        raw = sys.stdin.read() if src == "-" else open(src, encoding="utf-8").read()
        tasks = json.loads(raw)
    except (OSError, ValueError) as exc:  # JSONDecodeError ⊂ ValueError
        sys.stderr.write(f"task-reconcile: cannot read task JSON from {src!r}: {exc}\n")
        return 2
    sys.stdout.write(render_report(reconcile_tasks(tasks)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
