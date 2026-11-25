"""lib/red_check.py: a red GitHub Actions job on a pull request's head is the
pull request's own, unless the base branch's newest finished reading of that
job is red too.

Origin: V>> 2026-10-15, "GO FOR OPTION 2!", for the merge sweep (GRIP#8343,
``red_check_hold`` in bin/grip-pr-monitor): hold a pull request for a red
non-required check unless main carries the same red, so a red main never holds
its own fix. Then 2026-10-17, for every merger: the sweep held GRIP#8377 for a
red its rule caught, and a session merged the pull request by hand three
minutes after the verdict. This module is the same judgement for a session's
own merge. lib/merge_yourself.py declines on it, and
hooks/red-check-merge-gate.py denies a typed merge command on it.

The readings are the sweep's, call for call, so the two cannot disagree:

* the head's runs come from ``actions/runs?head_sha=``, never from the status
  rollup, which mirrors the merge box and can leave out a run (cli/cli#14253);
  every run short of a whole success is opened for its jobs;
* the base's reading of a job (the pull request's ``baseRefName``, ``main``
  for every GRIP pull request today) is its conclusion in the newest of the
  base's last ``MAIN_RUNS_LIMIT`` runs of the same workflow id in which the job
  finished, whatever the run's own state, and the walk stops at a whole-success
  run that lacks the job, because that version of the workflow does not run it;
* a job is red on ``failure``, ``timed_out`` or ``startup_failure``; a job
  still running is not red; names travel on tab-separated lines with tabs and
  line breaks folded to spaces first;
* once no red is the pull request's own, a run on the head that has not
  finished holds the merge too (the sweep's ``pending_check_hold``, V>>
  2026-10-26, GRIP#8682): any status but ``completed``, an unreadable one
  included. A cancelled run has finished, so cancelling a run that can never
  finish is the release. Both checks read the one listing, and its lines are
  split as bash's ``read`` splits them on a tab.

Three things the sweep does not need and a session's merge does (Codex and
AGORA on GRIP#8454): a host-qualified repository (``HOST/OWNER/REPO``) sends
every API read to that host; a ``deadline`` bounds the whole judgement, so a
hook with a fixed budget runs out of time as an unreadable verdict rather than
being killed without a trace; and a red of the pull request's own, once
confirmed, survives a later comparison that fails, because a fail-open caller
would otherwise lose a proven hold to a transient error.

Every ``gh`` call goes through an injectable ``run``, so the tests need no
network, and tests/test_grip_pr_monitor.py drives the sweep's own scenarios
through ``judge`` to pin the parity.
"""

from __future__ import annotations

import os
import re
import subprocess
import time
from dataclasses import dataclass, replace

DEFAULT_REPO = "grip-platform/GRIP"
#: The job conclusions that count as red.
RED = ("failure", "timed_out", "startup_failure")
#: The conclusions of a job that finished; anything else says nothing yet.
FINISHED = RED + ("success", "neutral", "action_required")
GH_TIMEOUT_SECONDS = 15
#: How many of the base's runs of a workflow the walk may open (the sweep's name).
ENV_LIMIT = "GRIP_PR_MONITOR_MAIN_RUNS"
DEFAULT_LIMIT = 50
#: GitHub's per_page ceiling, and so the walk's: a larger setting is read as this.
MAX_LIMIT = 100

_JQ_FLAT = r'def flat: gsub("[\\t\\n\\r]"; " ");'
#: The pull request's head commit and base branch as "sha<TAB>base".
HEAD_JQ = r'"\(.headRefOid)\t\(.baseRefName // "main")"'
#: The runs on a commit that may hold a red job or have not finished:
#: everything short of a whole success, as
#: "run<TAB>workflow_id<TAB>status<TAB>workflow" lines. A missing status reads
#: "unknown", never blank, since a blank middle field shifts the name into it.
HEAD_RUNS_JQ = (_JQ_FLAT + r' .workflow_runs[] | select(.conclusion != "success" and .conclusion != "skipped")'
                r' | "\(.id)\t\(.workflow_id)\t\((.status // "") | if . == "" then "unknown" else flat end)'
                r'\t\(.name // "" | flat)"')
#: A run's jobs as "job<TAB>conclusion" lines; a job still running has none.
RUN_JOBS_JQ = _JQ_FLAT + r'.jobs[] | "\(.name // "" | flat)\t\(.conclusion // "")"'
#: The base's runs of one workflow as "run<TAB>conclusion" lines, newest first.
MAIN_RUNS_JQ = r'.workflow_runs[] | "\(.id)\t\(.conclusion // "")"'
_SHA = re.compile(r"[0-9a-f]{40}")


class Unreadable(RuntimeError):
    """A gh read that failed, timed out, ran out of the deadline or answered an
    unexpected shape."""


@dataclass(frozen=True)
class RedJob:
    """One red job on the head: its workflow's id and name, and its own name."""

    workflow_id: str
    workflow: str
    job: str

    @property
    def label(self) -> str:
        return f'"{self.workflow} / {self.job}"'


@dataclass(frozen=True)
class Verdict:
    """Whether the pull request may merge for all its red jobs.

    ``hold`` is empty when it may; otherwise the sweep's own words for why not.
    ``unreadable`` marks a hold that is about a failed read, so a caller can
    fail open (a person at a keyboard) or closed (an unattended merger), and
    ``cause`` says what failed. ``own`` and ``mains`` label the reds that are
    the pull request's own and the reds that are the base's too; ``unread``
    labels reds whose comparison failed after a red of the pull request's own
    was already confirmed, which keeps the hold. ``base`` is the branch the
    reds were compared with. ``unfinished`` labels the runs on the head that
    have not finished, which hold the merge once no red is its own; an
    unreadable hold carries them too when the head's listing was read.
    """

    hold: str = ""
    unreadable: bool = False
    own: tuple[str, ...] = ()
    mains: tuple[str, ...] = ()
    unread: tuple[str, ...] = ()
    base: str = "main"
    cause: str = ""
    unfinished: tuple[str, ...] = ()

    @property
    def clear(self) -> bool:
        return not self.hold


def main_runs_limit(env: dict | None = None) -> int:
    """The walk's window: a positive integer from the environment, capped at
    GitHub's page size, else 50."""
    raw = (os.environ if env is None else env).get(ENV_LIMIT, "")
    return min(int(raw), MAX_LIMIT) if raw.isdigit() and int(raw) > 0 else DEFAULT_LIMIT


def split_host(repo: str) -> tuple[str, str]:
    """("github.example.com", "acme/service") for a host-qualified repository,
    ("", "owner/repo") otherwise: gh's ``-R`` accepts ``[HOST/]OWNER/REPO`` but
    an API path does not."""
    parts = repo.split("/")
    return (parts[0], "/".join(parts[1:])) if len(parts) == 3 else ("", repo)


def _pair(line: str) -> tuple[str, str]:
    """The first two tab-separated fields of a line, the second possibly empty."""
    first, _, second = line.partition("\t")
    return first, second.split("\t", 1)[0]


def _read_fields(line: str, count: int) -> list[str]:
    """The COUNT values bash's ``IFS=$'\\t' read -r`` gives its names. A tab is
    whitespace to ``read``, so leading and trailing tabs go, a run of tabs is
    one separator, and the last name keeps the rest of the line, inner tabs
    and all; names left over are empty."""
    rest = line.strip("\t")
    fields = []
    for _ in range(count - 1):
        field, _, rest = rest.partition("\t")
        fields.append(field)
        rest = rest.lstrip("\t")
    return fields + [rest]


def _first_by_name(pairs: list[tuple[str, str]]) -> dict[str, str]:
    """name -> value, keeping the first value a name appears with, as the
    sweep's awk does."""
    table: dict[str, str] = {}
    for name, value in pairs:
        table.setdefault(name, value)
    return table


class Reader:
    """The sweep's reads, each cached for one judgement, under one deadline."""

    def __init__(self, repo: str, run=subprocess.run, limit: int | None = None,
                 deadline: float | None = None) -> None:
        self.host, self.repo = split_host(repo)
        self.full = repo
        self.run = run
        self.limit = main_runs_limit() if limit is None else min(limit, MAX_LIMIT)
        self.deadline = deadline
        self._jobs: dict[str, list[tuple[str, str]]] = {}
        self._runs: dict[tuple[str, str], list[tuple[str, str]]] = {}
        self._heads: dict[str, list[list[str]]] = {}

    def _lines(self, args: list[str]) -> list[str]:
        """gh's stdout as non-empty lines; any failure, and a spent deadline,
        is Unreadable."""
        timeout: float = GH_TIMEOUT_SECONDS
        if self.deadline is not None:
            remaining = self.deadline - time.monotonic()
            if remaining <= 0:
                raise Unreadable("out of time")
            timeout = min(timeout, remaining)
        try:
            done = self.run(["gh", *args], capture_output=True, text=True, timeout=timeout)
        except Exception as exc:  # noqa: BLE001 -- a timeout or a missing binary reads as nothing
            raise Unreadable(f"{type(exc).__name__}: {exc}"[:160]) from exc
        if done.returncode != 0:
            raise Unreadable((done.stderr or "").strip()[:160] or f"gh exit {done.returncode}")
        return [line for line in (done.stdout or "").split("\n") if line]

    def _api(self, path: str, jq: str, *, paginate: bool = False, fields: tuple[str, ...] = ()) -> list[str]:
        """One REST read, on the repository's own host. ``fields`` are gh's
        ``-f``/``-F`` pairs, sent as an encoded query on a GET."""
        args = (["api"] + (["--paginate"] if paginate else []) + (["-X", "GET"] if fields else [])
                + (["--hostname", self.host] if self.host else []))
        return self._lines([*args, path, *fields, "--jq", jq])

    def head(self, pr: int) -> tuple[str, str]:
        """The pull request's head commit (a full SHA) and base branch."""
        lines = self._lines(["pr", "view", str(pr), "-R", self.full, "--json", "headRefOid,baseRefName",
                             "--jq", HEAD_JQ])
        sha, base = _pair(lines[0]) if lines else ("", "")
        if not _SHA.fullmatch(sha.strip()):
            raise Unreadable("no head commit")
        return sha.strip(), base.strip() or "main"

    def _run_job_pairs(self, run_id: str) -> list[tuple[str, str]]:
        """A run's jobs as (job, conclusion) pairs in the listing's order, read once."""
        if run_id not in self._jobs:
            lines = self._lines(["run", "view", run_id, "--repo", self.full, "--json", "jobs", "--jq", RUN_JOBS_JQ])
            self._jobs[run_id] = [_pair(line) for line in lines]
        return self._jobs[run_id]

    def run_jobs(self, run_id: str) -> dict[str, str]:
        """A run's jobs as job -> conclusion, the first value per name, as the
        sweep's awk reads a job in one of the base's runs."""
        return _first_by_name(self._run_job_pairs(run_id))

    def _reds_in(self, run_id: str, workflow_id: str, workflow: str) -> list[RedJob]:
        # Every job line, as the sweep's loop reads the head: two jobs in one run
        # can share a name, and a failed one after a passed one is still red
        # (Codex on GRIP#8454).
        return [RedJob(workflow_id, workflow, job) for job, conclusion in self._run_job_pairs(run_id)
                if job and conclusion in RED]

    def head_runs(self, sha: str) -> list[list[str]]:
        """Commit SHA's runs short of a whole success as [run, workflow_id,
        status, workflow], read once, so the red check and the unfinished check
        judge one listing."""
        if sha not in self._heads:
            lines = self._api(f"repos/{self.repo}/actions/runs?head_sha={sha}&per_page=100", HEAD_RUNS_JQ,
                              paginate=True)
            self._heads[sha] = [_read_fields(line, 4) for line in lines]
        return self._heads[sha]

    def red_head_jobs(self, sha: str) -> list[RedJob]:
        """Every red job on commit SHA, from its runs short of a whole success."""
        reds: list[RedJob] = []
        for run_id, workflow_id, _status, workflow in self.head_runs(sha):
            if run_id:
                reds.extend(self._reds_in(run_id, workflow_id, workflow))
        return reds

    def unfinished(self, sha: str) -> tuple[str, ...]:
        """The quoted names of the runs on commit SHA that have not finished."""
        return tuple(f'"{workflow}"' for run_id, _id, status, workflow in self.head_runs(sha)
                     if run_id and status != "completed")

    def known_unfinished(self, sha: str) -> tuple[str, ...]:
        """unfinished() from a listing already read, without reading it again."""
        return self.unfinished(sha) if sha in self._heads else ()

    def _base_runs(self, workflow_id: str, base: str) -> list[tuple[str, str]]:
        """The base's runs of the workflow as (run, whole conclusion), newest first."""
        if (workflow_id, base) not in self._runs:
            # The branch rides as a field gh encodes: git allows "#" and "&" in
            # a branch name, which in a URL end the path or start another
            # parameter (Codex on GRIP#8454).
            lines = self._api(f"repos/{self.repo}/actions/workflows/{workflow_id}/runs", MAIN_RUNS_JQ,
                              fields=("-f", f"branch={base}", "-F", f"per_page={self.limit}"))
            self._runs[(workflow_id, base)] = [_pair(line) for line in lines]
        return self._runs[(workflow_id, base)]

    def main_job_conclusion(self, workflow_id: str, job: str, base: str = "main") -> str:
        """JOB's conclusion in the newest of the base's runs of the workflow
        where it finished, "absent" when it finished in none the walk opened."""
        for run_id, whole in self._base_runs(workflow_id, base):
            if not run_id:
                continue
            found = self.run_jobs(run_id).get(job, "")
            if found in FINISHED:
                return found
            if whole == "success":
                break
        return "absent"


def _compare(reader: Reader, reds: list[RedJob], base: str) -> Verdict:
    """Sort the head's reds into the pull request's own and the base's own. A
    comparison that fails never discards a red already confirmed as the pull
    request's own; it only reads as unreadable when nothing was confirmed."""
    own: list[str] = []
    mains: list[str] = []
    unread: list[str] = []
    cause = ""
    for red in reds:
        try:
            on_base = reader.main_job_conclusion(red.workflow_id, red.job, base)
        except Unreadable as exc:
            unread.append(red.label)
            cause = cause or str(exc)
            continue
        (mains if on_base in RED else own).append(red.label)
    if own:
        hold = f"its red check(s) are not red on {base}: " + ", ".join(own)
        return Verdict(hold, False, tuple(own), tuple(mains), tuple(unread), base, cause)
    if unread:
        return Verdict(f"its red check {unread[0]} could not be compared with {base}", True, (), tuple(mains),
                       tuple(unread), base, cause)
    return Verdict("", False, (), tuple(mains), (), base)


def _with_unfinished(reader: Reader, sha: str, verdict: Verdict) -> Verdict:
    """An unreadable hold, carrying the runs the head's listing already showed
    still going: a caller that fails open on a failed read must still hold for
    a run it saw (Codex on GRIP#8454). The hold keeps the sweep's words."""
    waiting = reader.known_unfinished(sha)
    return replace(verdict, unfinished=waiting) if waiting else verdict


def judge(repo: str, pr: int, *, run=subprocess.run, limit: int | None = None,
          deadline: float | None = None) -> Verdict:
    """Is the pull request held for a red job of its own, or for a run on its
    head that has not finished? The sweep's answer, in the sweep's order,
    bounded by ``deadline`` (a ``time.monotonic`` instant) when one is given."""
    reader = Reader(repo, run, limit, deadline)
    try:
        sha, base = reader.head(pr)
    except Unreadable as exc:
        return Verdict("its head commit could not be read", True, cause=str(exc))
    try:
        reds = reader.red_head_jobs(sha)
    except Unreadable as exc:
        return _with_unfinished(reader, sha, Verdict("its checks could not be read", True, base=base,
                                                     cause=str(exc)))
    verdict = _compare(reader, reds, base)
    if verdict.unreadable:
        return _with_unfinished(reader, sha, verdict)
    waiting = () if verdict.hold else reader.unfinished(sha)
    if waiting:
        return Verdict("its checks have not finished: " + ", ".join(waiting), mains=verdict.mains, base=base,
                       unfinished=waiting)
    return verdict
