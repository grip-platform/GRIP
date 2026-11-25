"""lib/merge_yourself.py: a `(yours)` closer that is only a `gh pr merge` of a
pull request AGORA has passed is the session's own step.

Origin: V>> 2026-10-12, on reading `RECOMMENDED NEXT STEP (yours)` above
`gh pr merge 7513 -R grip-platform/GRIP --squash` at the end of a report:
"IF the recommended next step is a simple merge of a PR, which AGORA deemed
mergeable, then don't stop and ask me to type in the gh pr merge. Just do it
yourself!" Rule: rules/an-agora-mergeable-pr-is-merged-not-handed-over.md.

Two halves, kept apart so every path has a test without the network:

* ``parse_merge_command`` and ``judge`` are pure. ``judge`` takes what ``gh``
  answered (the pull request view, its issue comments, one GraphQL read of its
  review threads, reviews and last commit) and says whether the pull request is
  mergeable on the same terms the merge sweep applies: a genuine AGORA verdict
  for the CURRENT head of MERGE or DEFER CHANGES, at least two seats sitting,
  the required checks green, GitHub reporting it mergeable, no red Actions job
  of its own (lib/red_check.py: a red is the pull request's own unless its base
  branch's newest finished reading of that job is red too, V>> 2026-10-15), no
  run on its head still going (V>> 2026-10-26, GRIP#8682), no
  unresolved review thread, no Codex review still running by its review-state
  marker (GRIP#9283), and either a Codex review of the head or a head
  older than the Codex wait. A pull request that touches a merge-machinery
  path is never forced: the by-effect judgment stays with the session.
* ``probe_with_gh`` is the only I/O: three ``gh`` subprocesses, each with its
  own timeout, and the red-check reads behind them. Any failure declines. A
  probe that cannot see must never force a merge, so every error resolves to
  "not by this hook".

``decide`` joins them for hooks/next-step-recorder.py and returns the one-line
Stop reason the operator will read on screen: second person or none, no word
with a GRIP-only meaning (lib/operator_voice_detector.py).
"""

from __future__ import annotations

import json
import re
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime
from itertools import chain
from pathlib import Path

try:
    from lib import red_check
    from lib.agora_verdict_router import genuine_verdict_for_sha, head_sha_marker
except ImportError:  # imported as a top-level module from lib/
    import red_check
    from agora_verdict_router import genuine_verdict_for_sha, head_sha_marker

DEFAULT_REPO = "grip-platform/GRIP"
#: The checks branch protection requires on GRIP (the merge sweep's doctrine).
REQUIRED_CHECKS = ("leak-check", "Forward Anchors")
#: How long a pull request waits for Codex before merging on AGORA alone
#: (bin/grip-pr-monitor, CODEX_WAIT_S).
CODEX_WAIT_SECONDS = 20 * 60
CODEX_LOGINS = ("chatgpt-codex-connector", "chatgpt-codex-connector[bot]")
MERGEABLE_VERDICTS = ("MERGE", "DEFER_CHANGES")
#: GitHub's merge states that a `gh pr merge` goes through on.
MERGE_STATES_OK = ("CLEAN", "UNSTABLE", "HAS_HOOKS")
GH_TIMEOUT_SECONDS = 4
#: Set to "0" to leave every `(yours)` merge closer alone.
ENV_SWITCH = "GRIP_MERGE_YOURSELF"

_MERGE_RE = re.compile(r"^\s*(?:rtk\s+)?gh\s+pr\s+merge\s+(?P<pr>\d+)(?P<rest>(?:\s+\S+)*)\s*$")
_REPO_RE = re.compile(r"[\w.-]+/[\w.-]+")
#: `--auto` is not among these: it defers the merge to GitHub past every reading
#: made here, so a job still running could turn red after it (AGORA on GRIP#8454).
_ALLOWED_FLAGS = frozenset({"--squash", "--merge", "--rebase", "--delete-branch", "-d", "--admin"})
#: The whole probe's share of the Stop hook's 10 s (settings.json). Its three
#: reads and the red check run against this one deadline, so slow reads leave
#: the red check less time instead of pushing the hook past its timeout, where
#: it would be killed before it could carry the merge on (Codex on GRIP#8454).
PROBE_BUDGET_SECONDS = 8.5
#: The most the red check may take of what is left: past its deadline it reads
#: as unreadable, which declines like any other failed read.
RED_CHECK_BUDGET_SECONDS = 4.0
_SEATS_RE = re.compile(r"Speakers responded:\s*(\d+)\s*/\s*(\d+)")
_MACHINERY_LINE = re.compile(r"^MERGE_MACHINERY_RE='(?P<pattern>.+)'\s*$", re.MULTILINE)
#: Codex keeps one comment per pull request whose marker names the commit and
#: status of each review it runs; bin/grip-pr-monitor reads it the same way.
_CODEX_MARKER_RE = re.compile(r"codex-review-state:v1\s+(.*?)\s*-->")
#: The review entries a v1 marker carries; one that is present but not an
#: object cannot be read (Codex on GRIP#9293).
_REVIEW_KEYS = ("code_review", "security_review")
#: Codex's reply when it is out of review quota (bin/grip-pr-monitor).
_QUOTA_RE = re.compile(r"reached your Codex usage limits", re.IGNORECASE)
_GRAPHQL = (
    "query($owner: String!, $name: String!, $number: Int!) {"
    " repository(owner: $owner, name: $name) { pullRequest(number: $number) {"
    " reviewThreads(first: 100) { nodes { isResolved } }"
    " reviews(last: 50) { nodes { author { login } commit { oid } } }"
    " comments(first: 50) { nodes { author { login } updatedAt body } }"
    " recent: comments(last: 20) { nodes { author { login } createdAt body } }"
    " commits(last: 1) { nodes { commit { committedDate } } } } } }"
)


@dataclass(frozen=True)
class MergeCommand:
    """A closer that is nothing but `gh pr merge <number> [flags]`."""

    pr: int
    repo: str

    @property
    def label(self) -> str:
        """The repo-qualified number the canonical style uses: GRIP#7513."""
        return f"{self.repo.rsplit('/', 1)[-1]}#{self.pr}"


@dataclass(frozen=True)
class Probe:
    """What the pull request looked like, and whether the hook may act on it."""

    mergeable: bool
    reason: str
    verdict: str | None = None
    head: str = ""
    seats: int = 0
    #: The red jobs that are main's own, so the log says why they did not hold.
    red_on_main: tuple[str, ...] = ()

    def as_log(self) -> dict:
        return {"mergeable": self.mergeable, "reason": self.reason, "verdict": self.verdict,
                "head": self.head[:9], "seats": self.seats, "red_on_main": list(self.red_on_main)}


@dataclass(frozen=True)
class Decision:
    """The hook's outcome for a merge closer: block the Stop, or leave it as recorded."""

    block: bool
    reason: str
    log: dict = field(default_factory=dict)


def parse_merge_command(text: str) -> MergeCommand | None:
    """The merge command in a closer's step text, or None when the text is
    anything else: another command, a sentence, or a merge joined to more."""
    m = _MERGE_RE.match(text or "")
    if m is None:
        return None
    repo = DEFAULT_REPO
    tokens = m.group("rest").split()
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok in ("-R", "--repo"):
            if i + 1 >= len(tokens):
                return None
            repo, i = tokens[i + 1], i + 2
        elif tok.startswith("--repo="):
            repo, i = tok.split("=", 1)[1], i + 1
        elif tok in _ALLOWED_FLAGS:
            i += 1
        else:
            return None
    if not _REPO_RE.fullmatch(repo):
        return None
    return MergeCommand(pr=int(m.group("pr")), repo=repo)


def machinery_pattern(root: Path) -> re.Pattern | None:
    """The merge sweep's MERGE_MACHINERY_RE, read from bin/grip-pr-monitor so
    the two cannot drift; None when it cannot be read."""
    try:
        text = (root / "bin" / "grip-pr-monitor").read_text(encoding="utf-8")
        m = _MACHINERY_LINE.search(text)
        return re.compile(m.group("pattern")) if m else None
    except (OSError, re.error):
        return None


def _get(obj, *keys, default=None):
    """A nested read that tolerates None or a non-dict at any level."""
    cur = obj
    for key in keys:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(key)
    return default if cur is None else cur


def _required_checks(rollup: list) -> list[str]:
    """The required checks that are not green: absent counts as not green."""
    seen: dict[str, str] = {}
    for item in rollup or []:
        if isinstance(item, dict):
            name = item.get("name") or item.get("context") or ""
            seen[name] = str(item.get("conclusion") or item.get("state") or "").upper()
    return [name for name in REQUIRED_CHECKS if seen.get(name) != "SUCCESS"]


def _seats_for_head(comments: list, head: str) -> int:
    """How many seats sat in the newest AGORA comment that reviewed `head`."""
    marker = head_sha_marker(head)
    seats = 0
    for c in comments or []:
        body = _get(c, "body", default="")
        m = _SEATS_RE.search(body) if marker in body else None
        if m:
            seats = int(m.group(1))
    return seats


def _codex_reviewed_head(pull: dict, head: str) -> bool:
    for r in _get(pull, "reviews", "nodes", default=[]) or []:
        if _get(r, "author", "login", default="") in CODEX_LOGINS and _get(r, "commit", "oid") == head:
            return True
    return False


def _age_seconds(stamp, now: float) -> float | None:
    """How long before NOW an ISO-8601 time was, or None when it cannot be read."""
    try:
        return now - datetime.fromisoformat(str(stamp).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return None


def _head_age_seconds(pull: dict, now: float) -> float | None:
    """How old the head commit is, or None when its date cannot be read. This
    is its commit date, not its push, so a push alone does not restart the wait;
    Codex's marker covers that (GRIP#9283)."""
    nodes = _get(pull, "commits", "nodes", default=[]) or []
    return _age_seconds(_get(nodes[-1], "commit", "committedDate") if nodes else None, now)


def _json_or_none(raw: str):
    try:
        return json.loads(raw)
    except ValueError:
        return None


def _comment_markers(comment: dict, now: float) -> list[tuple[object, bool]]:
    """The review-state markers in one comment the connector wrote, each as
    (its JSON, or None when it cannot be parsed; whether the comment changed
    within the Codex wait). Any other comment gives []."""
    if _get(comment, "author", "login", default="") not in CODEX_LOGINS:
        return []
    age = _age_seconds(_get(comment, "updatedAt"), now)
    fresh = age is not None and age < CODEX_WAIT_SECONDS
    body = str(_get(comment, "body", default=""))
    return [(_json_or_none(raw), fresh) for raw in _CODEX_MARKER_RE.findall(body)]


def _reviews(marker) -> list[dict]:
    """The review entries of a parsed marker; [] when it is not an object."""
    return [r for r in marker.values() if isinstance(r, dict)] if isinstance(marker, dict) else []


def _running_commit(review: dict) -> str | None:
    """The commit a still-running review is on; "" when it has not started, has
    completed or has failed (Codex writes a review not started with every field
    null, and a review out of quota ends "failed" and never resumes, GRIP#9293);
    None when it runs but names no usable commit."""
    status = review.get("status")
    if status in (None, "completed", "failed"):
        return ""
    sha = review.get("commit_sha")
    return sha if isinstance(sha, str) and sha else None


def _unreadable_marker(marker) -> bool:
    """Whether a marker cannot be read: not an object, or an expected review
    entry that is present but not an object."""
    if not isinstance(marker, dict):
        return True
    return any(key in marker and not isinstance(marker[key], dict) for key in _REVIEW_KEYS)


def _running_reason(fresh: list, head: str) -> str:
    """Why a review the fresh markers show still running holds, or ""."""
    active = [_running_commit(r) for r in chain.from_iterable(map(_reviews, fresh))]
    running = [sha for sha in active if sha]
    if head and head in running:
        return f"Codex is still reviewing the newest commit ({head[:9]})"
    if running:
        return f"Codex is still reviewing an earlier commit ({running[0][:9]})"
    if None in active or any(map(_unreadable_marker, fresh)):
        return "Codex's review-state marker could not be read"
    return ""


def _connector_said(comments: list, pattern: re.Pattern, stamp: str) -> list[str]:
    """The STAMP field of each comment the connector wrote whose body matches PATTERN."""
    return [str(_get(c, stamp, default="")) for c in comments
            if _get(c, "author", "login", default="") in CODEX_LOGINS
            and pattern.search(str(_get(c, "body", default="")))]


def _quota_reply_after_marker(pull: dict) -> bool:
    """Whether Codex posted its usage-limit reply after its review-state marker
    last changed: the review cannot finish, so the sweep merges on AGORA
    alone, and so may this probe (Codex on GRIP#9293)."""
    marked = _connector_said(_get(pull, "comments", "nodes", default=[]) or [], _CODEX_MARKER_RE, "updatedAt")
    replies = _connector_said(_get(pull, "recent", "nodes", default=[]) or [], _QUOTA_RE, "createdAt")
    return bool(marked) and any(reply >= max(marked) for reply in replies)


def _codex_still_reviewing(pull: dict, head: str, now: float) -> str:
    """Why Codex's review-state marker holds the merge, or "" (GRIP#9283): a
    review still running on the head or on an earlier commit, a marker that
    cannot be read, or a marker with no review of the head while Codex changed
    it, or the head was committed, within the wait (Codex on GRIP#9293)."""
    comments = _get(pull, "comments", "nodes", default=[]) or []
    found = list(chain.from_iterable(_comment_markers(c, now) for c in comments))
    fresh = [marker for marker, is_fresh in found if is_fresh]
    reason = _running_reason(fresh, head)
    if reason or not head or not found:
        return reason
    reached = any(r.get("commit_sha") == head for r in chain.from_iterable(_reviews(m) for m, _ in found))
    age = _head_age_seconds(pull, now)
    if not reached and (fresh or (age is not None and age < CODEX_WAIT_SECONDS)):
        return f"Codex has not reached the newest commit ({head[:9]}) yet"
    return ""


def _codex_wait_remaining(pull: dict, head: str, now: float) -> int:
    """Seconds of the Codex wait still to run: 0 once Codex reviewed the head
    or the head is older than the wait; the whole wait when the date is unreadable."""
    if _codex_reviewed_head(pull, head):
        return 0
    age = _head_age_seconds(pull, now)
    if age is None:
        return CODEX_WAIT_SECONDS
    return max(0, int(CODEX_WAIT_SECONDS - age))


def _github_decline(view: dict) -> str:
    """Why GitHub's own view of the pull request rules the merge out, or ""."""
    if view.get("state") != "OPEN" or view.get("isDraft"):
        state = "a draft" if view.get("isDraft") else str(view.get("state")).lower()
        return f"the pull request is {state}"
    if view.get("mergeable") != "MERGEABLE" or view.get("mergeStateStatus") not in MERGE_STATES_OK:
        return f"GitHub reports it {view.get('mergeable')}/{view.get('mergeStateStatus')}"
    missing = _required_checks(view.get("statusCheckRollup") or [])
    return "required checks not green: " + ", ".join(missing) if missing else ""


def _red_decline(red: red_check.Verdict | None) -> str:
    """Why a red Actions job, or a run on the head that has not finished, rules
    the merge out, or "". A read that failed declines too: a probe that cannot
    see must never force a merge. None means the red check was not probed (the
    pure tests), which declines nothing."""
    return red.hold if red is not None else ""


def _machinery_decline(view: dict, machinery: re.Pattern | None) -> str:
    """Why the by-effect judgment stays with the session, or ""."""
    if machinery is None:
        return "the merge-machinery list could not be read"
    touched = [_get(f, "path", default="") for f in view.get("files") or []]
    held = [p for p in touched if p and machinery.search(p)]
    if not held:
        return ""
    return "it touches merge machinery (" + ", ".join(held[:3]) + "), which the session judges by effect"


def _review_decline(pull: dict, head: str, now: float) -> str:
    """Why the review state rules the merge out, or ""."""
    threads = _get(pull, "reviewThreads", "nodes", default=[]) or []
    unresolved = [t for t in threads if not _get(t, "isResolved", default=False)]
    if unresolved:
        return f"{len(unresolved)} review thread(s) are unresolved"
    reviewing = _codex_still_reviewing(pull, head, now)
    if reviewing:
        return "" if _quota_reply_after_marker(pull) else reviewing
    remaining = _codex_wait_remaining(pull, head, now)
    return f"no Codex review of the head yet, {remaining} s of the Codex wait remain" if remaining else ""


def judge(view: dict, comments: list, graphql: dict, *, root: Path, now: float | None = None,
          red: red_check.Verdict | None = None) -> Probe:
    """Pure: is the pull request mergeable on the merge sweep's terms? ``red``
    is lib/red_check's verdict on the head's Actions jobs, or None when it was
    not probed."""
    now = time.time() if now is None else now
    head = str(view.get("headRefOid") or "")
    pull = _get(graphql, "data", "repository", "pullRequest", default={}) or {}
    verdict = genuine_verdict_for_sha(comments or [], head) if head else None
    seats = _seats_for_head(comments or [], head) if head else 0
    mains = red.mains if red is not None else ()
    why = (_github_decline(view) or _red_decline(red) or _machinery_decline(view, machinery_pattern(root))
           or _review_decline(pull, head, now))
    if why:
        return Probe(False, why, verdict, head, seats, mains)
    if verdict not in MERGEABLE_VERDICTS:
        return Probe(False, f"no mergeable AGORA verdict for the head ({verdict or 'none'})", verdict, head,
                     seats, mains)
    if seats < 2:
        return Probe(False, f"only {seats} seat(s) sat, below the quorum of two", verdict, head, seats, mains)
    own = ", no red check of its own, no run still going" if red is not None else ""
    return Probe(True, f"{verdict.replace('_', ' ')} on {head[:9]}, {seats} seats sitting, required checks "
                       f"green{own}, no open review threads", verdict, head, seats, mains)


def _gh_json(args: list[str], run, deadline: float) -> object:
    timeout = min(GH_TIMEOUT_SECONDS, deadline - time.monotonic())
    if timeout <= 0:
        raise RuntimeError("out of time")
    done = run(["gh", *args], capture_output=True, text=True, timeout=timeout)
    if done.returncode != 0:
        raise RuntimeError((done.stderr or "").strip()[:160] or f"gh exit {done.returncode}")
    return json.loads(done.stdout)


def probe_with_gh(cmd: MergeCommand, *, root: Path, run=subprocess.run, now: float | None = None) -> Probe:
    """Read the pull request through `gh` and judge it; any failure declines."""
    owner, name = cmd.repo.split("/", 1)
    deadline = time.monotonic() + PROBE_BUDGET_SECONDS
    try:
        view = _gh_json(["pr", "view", str(cmd.pr), "-R", cmd.repo, "--json",
                         "state,isDraft,headRefOid,mergeable,mergeStateStatus,files,statusCheckRollup"], run, deadline)
        comments = _gh_json(["api", f"repos/{cmd.repo}/issues/{cmd.pr}/comments", "--paginate"], run, deadline)
        graphql = _gh_json(["api", "graphql", "-F", f"owner={owner}", "-F", f"name={name}",
                            "-F", f"number={cmd.pr}", "-f", f"query={_GRAPHQL}"], run, deadline)
    except Exception as exc:  # noqa: BLE001 -- a probe that cannot see declines
        return Probe(False, f"the probe could not read the pull request: {type(exc).__name__}: {exc}"[:200])
    if not isinstance(view, dict) or not isinstance(comments, list) or not isinstance(graphql, dict):
        return Probe(False, "the probe read an unexpected shape from gh")
    try:
        red = red_check.judge(cmd.repo, cmd.pr, run=run,
                              deadline=min(deadline, time.monotonic() + RED_CHECK_BUDGET_SECONDS))
    except Exception as exc:  # noqa: BLE001 -- a read like the others: a failure declines, never raises
        return Probe(False, f"the red check could not run: {type(exc).__name__}: {exc}"[:200])
    return judge(view, comments, graphql, root=root, now=now, red=red)


def block_reason(cmd: MergeCommand, probe: Probe) -> str:
    """One plain line for the operator's screen: what is happening and why."""
    return (f"{cmd.label} is mergeable on AGORA's verdict ({probe.reason}): merging it is this "
            "session's own step, carried on now.")


def decide(step_text: str, *, root: Path, probe=probe_with_gh, now: float | None = None) -> Decision | None:
    """The hook's decision for a `(yours)` step: None when the step is not a
    plain merge command; otherwise block with the reason, or decline with why."""
    cmd = parse_merge_command(step_text)
    if cmd is None:
        return None
    result = probe(cmd, root=root, now=now)
    log = {"pr": cmd.pr, "repo": cmd.repo, **result.as_log()}
    if not result.mergeable:
        return Decision(False, result.reason, log)
    return Decision(True, block_reason(cmd, result), log)
