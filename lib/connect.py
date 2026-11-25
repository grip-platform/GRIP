"""/connect: a planner, a coder and a reviewer from three vendors, relayed by a conductor.

A planner (one text-only call) writes a plan. A coding agent in another harness
makes the change in a sandboxed worktree. The conductor checks the change with
no model, and a reviewer from a third vendor reads the diff. Only the conductor
touches git, the mesh room and the seal chain, and no model text reaches a shell
string. The brakes are a per-run cost cap, a time limit per step, one repair
round, a stop marker and a veto in the run's mesh room. The conductor never
pushes, opens a pull request or merges: /finish and AGORA own those.

Design: Fable 5.2's /connect position of 2026-11-11, sealed by the /ultraplan
trio, and the operator's picks the same day -- the conductor and its seal chain,
the role rule with billing (lib/connect_roles.py), the model-free check
(lib/connect_check.py) and review, and the cost cap and time limits. The seats
are one-shot: a stateful foreign peer, turn-taking, and seats that post to the
mesh themselves stay out until the BLOCKED count shows they are needed.

Usage (bin/grip-connect):
    grip-connect run "<task>" [--repo PATH] [--base REF] [--file PATH ...] [--roots lib/,tests/]
    grip-connect plan "<task>" ...         admit and plan only; nothing is changed
    grip-connect roster                    the seats assign() picks, and what stopped the others
    grip-connect status [RUN_ID]
    grip-connect stop [RUN_ID] [--clear]
    grip-connect verify RUN_ID
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import posixpath
import re
import secrets
import sys
import textwrap
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from lib import connect_check as chk  # noqa: E402
from lib.connect_roles import (  # noqa: E402
    ROLES, Budget, BudgetRefused, Roles, Seat, assign, explain, load_roles)
from lib._compat import windows_pid_alive  # noqa: E402

CONDUCTOR = "grip-connect"
MAX_SEAT_TRIES = 3
TASK_LIMIT = 8_000
PACK_LIMIT = 120_000
TREE_LIMIT = 30_000
FILE_LIMIT = 40_000
MAX_FILES = 20
MAX_CHANGE_BYTES = 1_000_000
OPEN_READS = ("the coder's sandbox confines writes and the network but not reads (measured on "
              "codex-cli 0.156.1), so a prompt-injected coder could read files outside its worktree "
              "and send them to its vendor. Pass --accept-open-reads to run anyway; GRIP#9154 tracks "
              "read confinement.")
DEFAULT_ROOTS = ("lib/", "tests/", "bin/", "skills/", "docs/", "scripts/")
_PLAN_LISTS = ("steps", "files_allowed", "test_paths", "cases", "risks")


class ConnectRefused(RuntimeError):
    """The run was not admitted. Nothing ran."""


class Halt(RuntimeError):
    """The run stops early with ``outcome``: STOPPED, BLOCKED, REFUSED or FAIL."""

    def __init__(self, outcome: str, why: str) -> None:
        super().__init__(why)
        self.outcome = outcome


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return sha256_bytes(text.encode("utf-8"))


# --- the run's parts -----------------------------------------------------------------


@dataclass(frozen=True)
class Request:
    task: str
    repo: Path
    base: str = "origin/main"
    files: tuple = ()
    roots: tuple = DEFAULT_ROOTS
    plan_only: bool = False
    accept_open_reads: bool = False


@dataclass
class Deps:
    """Everything that reaches outside the process. The tests replace each one."""

    roles: Roles
    state_dir: Path
    worktrees_dir: Path
    call: Callable      # (seat, prompt, timeout) -> (text, served model)
    code: Callable      # (run, seat, prompt, attempt) -> CoderResult
    tests: Callable     # (run, worktree, test_paths, timeout) -> chk.Finished
    seal: Callable      # (decision, inputs, predecessor id, depth) -> leaf dict with "id"
    home: Callable      # () -> (index, Path) of a ready Codex home, or None
    shield: Callable    # (text) -> the text an outside model may receive
    mesh_db: str | None = None
    poll: float = 5.0
    fetch: bool = True


@dataclass
class Reply:
    text: str
    served: str
    ms: int
    reserved_micro: int
    prompt_sha256: str
    sent_sha256: str
    sent: str = ""

    def facts(self) -> dict:
        return {"served": self.served, "ms": self.ms, "reserved_micro_usd": self.reserved_micro,
                "prompt_sha256": self.prompt_sha256, "sent_sha256": self.sent_sha256,
                "reply_sha256": sha256_text(self.text), "reply": self.text[:20_000]}


@dataclass
class CoderResult:
    finished: chk.Finished
    last_message: str
    provider: str
    served: str

    @property
    def question(self) -> str:
        found = re.search(r"^BLOCKED:\s*(.+)$", self.last_message, re.M)
        return found.group(1).strip()[:500] if found else ""

    def facts(self) -> dict:
        return {**self.finished.facts(), "provider": self.provider, "served": self.served,
                "last_message": self.last_message[:4000], "blocked": bool(self.question)}


@dataclass
class Run:
    id: str
    req: Request
    deps: Deps
    dir: Path
    room: str
    roster: dict
    budget: Budget
    lock: "RepoLock"
    wt: Path
    base_commit: str
    home: tuple
    task_sha: str
    forbidden: Callable
    leaves: list = field(default_factory=list)
    notes: list = field(default_factory=list)
    unavailable: set = field(default_factory=set)
    served: dict = field(default_factory=dict)
    tested_tree: str = ""
    vetoed: str = ""


# --- brakes: the stop marker, the room's veto, the lock ----------------------------------------


def stop_reason(state_dir, run_id: str = "") -> str:
    """Why a run must stop now, from the stop markers, or ""."""
    names = ["STOP"] + ([f"STOP-{run_id}"] if run_id else [])
    hit = [name for name in names if (Path(state_dir) / name).exists()]
    return f"stop marker {hit[0]} is set" if hit else ""


def _vetoed(r: Run) -> str:
    """The room can stop a run and can never advance one: only ``veto`` rows are read."""
    from lib import mesh_room

    if r.vetoed:
        return r.vetoed  # sticky: reading the room advanced its cursor past the veto
    rows = mesh_room.read_new(CONDUCTOR, room=r.room, advance=True, db=r.deps.mesh_db)
    vetoes = [row for row in rows if row["kind"] == "veto"]
    r.vetoed = f"vetoed in room {r.room} (message {vetoes[0]['seq']})" if vetoes else ""
    return r.vetoed


def _stop_why(r: Run) -> str:
    return stop_reason(r.deps.state_dir, r.id) or _vetoed(r)


def _checkpoint(r: Run) -> None:
    why = _stop_why(r)
    if why:
        raise Halt("STOPPED", why)


def _alive(pid) -> bool:
    """True while the process that holds the repository lock still runs.

    The lock records that run's ``os.getpid()``. On Windows ``os.kill(pid, 0)`` is a
    console Ctrl+C call, not a check: for a PID that names no console process group
    it raises OSError (winerror 87), which escaped this function, so a lock left by
    an earlier run stopped every later run (GRIP#8883). There the Windows PID is
    looked up with ``windows_pid_alive``; when the lookup cannot tell, the answer is
    True, so a run is refused rather than a live holder's lock reclaimed. Off
    Windows the signal probe is unchanged.
    """
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return False
    if sys.platform == "win32":
        try:
            return windows_pid_alive(pid)
        except OSError:
            return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class RepoLock:
    """One run per repository at a time: an O_EXCL file holding the pid and the start time."""

    def __init__(self, state_dir, repo) -> None:
        self.path = Path(state_dir) / f"lock-{sha256_text(str(Path(repo).resolve()))[:12]}"

    def _create(self, run_id: str) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            return False
        with os.fdopen(fd, "w") as fh:
            json.dump({"pid": os.getpid(), "run": run_id, "started": int(time.time())}, fh)
        return True

    def holder(self) -> dict:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def acquire(self, run_id: str) -> str:
        """Take the lock, reclaiming it from a dead holder. Returns a note for the record."""
        if self._create(run_id):
            return ""
        held = self.holder()
        if _alive(held.get("pid")):
            raise ConnectRefused(f"another /connect run holds this repository "
                                 f"(run {held.get('run')}, pid {held.get('pid')})")
        self.path.unlink(missing_ok=True)
        if not self._create(run_id):
            raise ConnectRefused("another run took the lock while a dead holder was reclaimed")
        return f"reclaimed the lock from dead pid {held.get('pid')} (run {held.get('run')})"

    def release(self) -> None:
        if self.holder().get("pid") == os.getpid():
            self.path.unlink(missing_ok=True)


# --- sealing and the room ----------------------------------------------------------------


def _post(r: Run, kind: str, body: str) -> None:
    from lib import mesh_room

    mesh_room.post(CONDUCTOR, "grip-connect conductor", body, author_kind="agent", kind=kind,
                   room=r.room, model="none (deterministic conductor)", db=r.deps.mesh_db)


def _decision(r: Run, step: str, sha: str, seat: Seat | None, served: str) -> dict:
    """What the signed leaf says: hashes, ids and model names; never prose, never a float."""
    out = {"run": r.id, "step": step, "artefact_sha256": sha}
    if seat:
        out.update({"seat": seat.public(), "served": served})
    return out


def _record(r: Run, step: str, artefact: dict, seat: Seat | None = None, served: str = "") -> None:
    """Write the step's artefact, seal it into a leaf linked to the last, and say so in the room."""
    path = r.dir / f"{len(r.leaves):02d}-{step}.json"
    artefact = {**artefact, "notes": list(r.notes)}
    r.notes.clear()
    path.write_text(json.dumps(artefact, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
                    encoding="utf-8")
    sha = sha256_bytes(path.read_bytes())
    prev = r.leaves[-1]["id"] if r.leaves else None
    leaf = r.deps.seal(_decision(r, step, sha, seat, served),
                       {"run": r.id, "task_sha256": r.task_sha}, prev, len(r.leaves))
    with open(r.dir / "leaves.jsonl", "a", encoding="utf-8") as fh:
        fh.write(json.dumps(leaf, sort_keys=True, ensure_ascii=False) + "\n")
    r.leaves.append({"step": step, "file": path.name, "sha256": sha, "id": leaf["id"]})
    who = f"; {seat.role} {seat.id} ({seat.vendor}), served {served}" if seat else ""
    _post(r, "say", f"{step} sealed: artefact {sha[:12]}, leaf {leaf['id']}{who}")


def _roster_line(roster: dict) -> str:
    return ", ".join(f"{role} {seat.id} ({seat.vendor}, {seat.billing})" for role, seat in roster.items())


# --- admit -------------------------------------------------------------------------------


def new_run_id() -> str:
    return time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + "-" + secrets.token_hex(3)


def _validate(req: Request) -> None:
    if not req.task.strip():
        raise ConnectRefused("the task is empty")
    if len(req.task) > TASK_LIMIT:
        raise ConnectRefused(f"the task is longer than {TASK_LIMIT} characters")
    if not (Path(req.repo) / ".git").exists():
        raise ConnectRefused(f"{req.repo} is not a git checkout")
    if not req.plan_only and not req.accept_open_reads:
        raise ConnectRefused(OPEN_READS)
    unsafe = chk.unsafe_drivers(req.repo)
    if unsafe:
        raise ConnectRefused("the repository has git run a command that the coder could edit, and "
                             "the conductor runs git outside the sandbox: " + "; ".join(unsafe))


def _no_roster(roles: Roles, unavailable) -> str:
    closest = ["; ".join(found) for _, found in explain(roles, unavailable=unavailable)[:3]]
    coder = ("no Codex subscription home is ready (each is full, holds credits, or did not "
             "answer). " if unavailable else "")
    return f"no roster passes the role rule. {coder}Closest: " + " | ".join(closest)


def _fetch(req: Request) -> str:
    if not req.base.startswith("origin/"):
        return "no fetch: the base is not on origin"
    try:
        chk.git(req.repo, "fetch", "--quiet", "origin", req.base.split("/", 1)[1], timeout=90)
    except Exception as exc:  # noqa: BLE001 -- fail-open: base on the last fetched ref
        return f"fetch failed, so the base is the last fetched ref: {type(exc).__name__}"
    return "fetched"


def _admit_facts(r: Run, fetched: str) -> dict:
    return {"run": r.id, "task_sha256": r.task_sha, "task": r.req.task, "repo": Path(r.req.repo).name,
            "base": r.req.base, "base_commit": r.base_commit, "fetch": fetched,
            "roster": {role: seat.public() for role, seat in r.roster.items()},
            "cap_micro_usd": r.budget.cap_micro, "step_seconds": r.deps.roles.step_seconds,
            "repair_rounds": r.deps.roles.repair_rounds, "coder_home_index": r.home[0],
            "roots": list(r.req.roots), "context_files": list(r.req.files),
            "accept_open_reads": r.req.accept_open_reads}


def _open(req: Request, deps: Deps, run_id: str, lock: RepoLock, note: str) -> Run:
    home = deps.home()
    unavailable = set() if home else {seat.id for seat in deps.roles.seats["coder"]}
    roster = assign(deps.roles, unavailable=unavailable)
    if roster is None:
        raise ConnectRefused(_no_roster(deps.roles, unavailable))
    fetched = _fetch(req) if deps.fetch else "no fetch"
    base_commit = chk.git(req.repo, "rev-parse", "--verify", req.base + "^{commit}").strip()
    run_dir = deps.state_dir / "runs" / run_id
    run_dir.mkdir(parents=True)
    wt = chk.make_worktree(req.repo, base_commit, Path(deps.worktrees_dir) / f"connect-{run_id}")
    r = Run(id=run_id, req=req, deps=deps, dir=run_dir, room=f"connect-{run_id}", roster=roster,
            budget=Budget(deps.roles.cap_micro), lock=lock, wt=wt, base_commit=base_commit,
            home=home, task_sha=sha256_text(req.task), forbidden=chk.forbidden_matcher(req.repo),
            notes=[note] if note else [])
    _post(r, "join", f"/connect run {run_id} admitted: {_roster_line(roster)}")
    _record(r, "admit", _admit_facts(r, fetched))
    return r


def admit(req: Request, deps: Deps) -> Run:
    """Check the brakes, take the lock, pick the seats and build the worktree."""
    _validate(req)
    why = stop_reason(deps.state_dir)
    if why:
        raise ConnectRefused(why)
    run_id = new_run_id()
    lock = RepoLock(deps.state_dir, req.repo)
    note = lock.acquire(run_id)
    try:
        return _open(req, deps, run_id, lock, note)
    except BaseException:
        lock.release()
        raise


# --- calling a text seat ------------------------------------------------------------------


def _known_vendor(model: str) -> str:
    from lib.roundtable import VENDOR, vendor_of

    bare = str(model).rsplit("/", 1)[-1]
    vendor = vendor_of(bare)
    return "" if vendor == bare and bare not in VENDOR else vendor


def _served_fault(seat: Seat, served: str, text: str) -> str:
    """R1 again, on what actually answered: a known other vendor is a failure."""
    if not str(text or "").strip():
        return "empty answer"
    vendor = _known_vendor(served)
    return f"served by {served} ({vendor}), not {seat.vendor}" if vendor and vendor != seat.vendor else ""


def _into(box: dict, fn, *args) -> None:
    try:
        box["value"] = fn(*args)
    except BaseException as exc:  # noqa: BLE001 -- carried back to the conductor's thread
        box["error"] = exc


def _braked_call(r: Run, seat: Seat, sent: str, timeout: int) -> tuple:
    """Call a text seat on a daemon thread and poll the brakes while it runs.

    A stop or a veto abandons the call: its answer can no longer change
    anything, and a daemon thread never holds the process open at exit.
    """
    box: dict = {}
    worker = threading.Thread(target=_into, args=(box, r.deps.call, seat, sent, timeout), daemon=True)
    worker.start()
    while worker.is_alive():
        worker.join(r.deps.poll)
        why = _stop_why(r) if worker.is_alive() else ""
        if why:
            raise Halt("STOPPED", f"{why}; the {seat.role} call was abandoned")
    if "error" in box:
        raise box["error"]
    return box["value"]


def _attempt(r: Run, seat: Seat, prompt: str, timeout: int, accept) -> tuple:
    sent = r.deps.shield(prompt)
    try:
        reserved = r.budget.reserve(seat, len(sent.encode("utf-8")))
    except BudgetRefused as exc:
        return None, str(exc)
    start = time.monotonic()
    try:
        text, served = _braked_call(r, seat, sent, timeout)
    except Halt:
        raise
    except Exception as exc:  # noqa: BLE001 -- a dead seat is a recorded failure, not a crash
        return None, f"{type(exc).__name__}: {exc}"[:300]
    why = _served_fault(seat, served, text) or accept(str(text))
    reply = Reply(str(text), str(served or seat.model), int((time.monotonic() - start) * 1000),
                  reserved, sha256_text(prompt), sha256_text(sent), sent)
    return (None, why) if why else (reply, "")


def _reassign(r: Run) -> None:
    pinned = {role: r.roster[role].id for role in r.served}
    roster = assign(r.deps.roles, unavailable=r.unavailable, pinned=pinned)
    if roster is None:
        raise Halt("FAIL", "no roster is left after seat failures: " + "; ".join(r.notes[-3:]))
    if roster != r.roster:
        r.notes.append("re-assigned: " + _roster_line(roster))
    r.roster = roster


def _call_role(r: Run, role: str, prompt: str, timeout: int, accept) -> tuple:
    """Call the roster's seat for ``role``; on failure re-assign and try again, bounded."""
    for _ in range(MAX_SEAT_TRIES):
        seat = r.roster[role]
        reply, why = _attempt(r, seat, prompt, timeout, accept)
        if reply:
            r.served[role] = reply.served
            return seat, reply
        r.notes.append(f"{role} seat {seat.id} failed: {why}")
        r.unavailable.add(seat.id)
        _reassign(r)
    raise Halt("FAIL", f"no {role} seat answered in {MAX_SEAT_TRIES} tries")


def parse_json(text: str):
    """The first JSON object in a model's reply, fenced or bare; None when there is none."""
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    start, end = text.find("{"), text.rfind("}")
    candidates = ([fenced.group(1)] if fenced else []) + ([text[start:end + 1]] if 0 <= start < end else [])
    for candidate in candidates:
        try:
            value = json.loads(candidate)
        except ValueError:
            continue
        if isinstance(value, dict):
            return value
    return None


# --- plan ---------------------------------------------------------------------------------

PLAN_CONTRACT = """You are the planner seat of /connect. You cannot run tools. Plan one small change
that a coding agent in another harness will make in this repository.

Return ONE JSON object and nothing else:
{"goal": "one line", "steps": ["..."], "files_allowed": ["path/relative/to/root", "..."],
 "test_paths": ["tests/test_example.py"], "cases": ["what each test checks"], "risks": ["..."]}

Rules:
- files_allowed lists every file the coder may create or change. Nothing else may change.
- test_paths lists pytest files named test_*.py, inside files_allowed. They must FAIL on the
  current code and PASS once the change is made, so the check can prove the change.
- Keep the change small. Never name hooks, CI workflows, settings, rules, agent instructions
  or encrypted files.
- Bracketed placeholders such as [PERSON-1] are redaction marks added before you saw the text.
- If the task cannot be done safely, return an empty goal and say why in risks.

TASK:
{task}

CONTEXT:
{pack}
"""


def _fill(template: str, **values: str) -> str:
    """Fill ``{name}`` slots in one pass, so text inside a value is never filled again."""
    return re.sub(r"\{(\w+)\}", lambda m: values.get(m.group(1), m.group(0)), template)


def plan_prompt(task: str, pack: str) -> str:
    return _fill(PLAN_CONTRACT, task=task, pack=pack)


def _file_block(wt: Path, rel: str) -> str:
    path = (wt / rel).resolve()
    if not path.is_relative_to(wt.resolve()) or not path.is_file():
        return f"FILE {rel}: not in the worktree (missing, outside it, or encrypted)"
    return f"FILE {rel}:\n{path.read_text(encoding='utf-8', errors='replace')[:FILE_LIMIT]}"


def _context_pack(r: Run) -> str:
    listing = chk.git(r.wt, "ls-files", "-t").splitlines()
    tree = "\n".join(line[2:] for line in listing if line.startswith("H "))[:TREE_LIMIT]
    parts = [f"TRACKED FILES (encrypted files left out):\n{tree}"]
    parts += [_file_block(r.wt, rel) for rel in r.req.files]
    return "\n\n".join(parts)[:PACK_LIMIT]


def _str_list(value) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def _shape(raw) -> list:
    if not isinstance(raw, dict):
        return ["the planner returned no JSON object"]
    problems = [f"{key} must be a list of strings" for key in _PLAN_LISTS if not _str_list(raw.get(key))]
    goal = raw.get("goal")
    if not isinstance(goal, str) or not goal.strip():
        problems.append("the goal is empty, so the planner declined: " + "; ".join(map(str, raw.get("risks") or [])))
    return problems


def _clean_path(path: str) -> str:
    """A normalised relative path, or "" when it leaves the repository."""
    norm = posixpath.normpath(path.strip())
    return "" if norm.startswith(("/", "..")) or norm in (".", "") else norm


def _scope(paths, roots, forbidden, encrypted) -> tuple:
    """(kept paths, problems): out-of-root paths are dropped; a protected path refuses the plan."""
    clean = [_clean_path(p) for p in paths]
    problems = [f"a path leaves the repository: {p!r}" for p, c in zip(paths, clean) if not c]
    problems += [f"names a protected path (CI, hooks, gates, rules, settings): {c}" for c in clean
                 if c and forbidden(c)]
    problems += [f"names a git-crypt path: {c}" for c in clean if c in encrypted]
    return sorted({c for c in clean if c and c.startswith(tuple(roots))}), problems


def _test_problems(tests, kept) -> list:
    if not tests:
        return ["test_paths is empty, so the check cannot prove the change"]
    named = [t for t in tests if not re.fullmatch(r"test_[^/]*\.py", posixpath.basename(t))]
    outside = [t for t in tests if t not in kept]
    return ([f"not a pytest file named test_*.py: {t}" for t in named] +
            [f"a test path is outside the plan's kept scope: {t}" for t in outside])


def validate_plan(raw, r: Run) -> tuple:
    """(plan, problems), with no model: the shape, the scope, the tests."""
    problems = _shape(raw)
    if problems:
        return {}, problems
    encrypted = set(chk.git_crypt_paths(r.wt, [_clean_path(p) for p in raw["files_allowed"]]))
    kept, problems = _scope(raw["files_allowed"], r.req.roots, r.forbidden, encrypted)
    dropped = sorted({_clean_path(p) for p in raw["files_allowed"]} - set(kept) - {""})
    if dropped:
        r.notes.append("dropped from scope, outside the allowed roots: " + ", ".join(dropped))
    tests = sorted({_clean_path(t) for t in raw["test_paths"]} - {""})
    problems += _test_problems(tests, kept)
    problems += [f"more than {MAX_FILES} files"] if len(kept) > MAX_FILES else []
    plan = {**{key: raw[key] for key in _PLAN_LISTS}, "goal": raw["goal"].strip(),
            "files_allowed": kept, "test_paths": tests}
    return plan, problems


def _plan_reply_fault(text: str) -> str:
    return "" if isinstance(parse_json(text), dict) else "the reply holds no JSON object"


def _plan(r: Run) -> dict:
    _checkpoint(r)
    prompt = plan_prompt(r.req.task, _context_pack(r))
    seat, reply = _call_role(r, "planner", prompt, r.deps.roles.step_seconds["plan"], _plan_reply_fault)
    plan, problems = validate_plan(parse_json(reply.text), r)
    _record(r, "plan", {"plan": plan, "problems": problems, **reply.facts()}, seat, reply.served)
    if problems:
        raise Halt("REFUSED", "the plan was refused: " + "; ".join(problems))
    return plan


# --- run: the coder ---------------------------------------------------------------------------

CODER_CONTRACT = """You are the coder seat of /connect, working in this directory only.
Make the change the plan below describes.

- Create or change ONLY these files: {files}
- Write the tests in test_paths so they fail on the current code and pass after your change.
- Do not run git commands that write (add, commit, stash, checkout): the conductor commits.
- Bracketed placeholders such as [PERSON-1] are redaction marks. Never copy one into a file.
- The network is off. If you cannot go on without an answer, stop and make the last line of
  your final message: BLOCKED: <your question>

PLAN (JSON):
{plan}
"""


def coder_prompt(plan: dict, feedback: str) -> str:
    text = _fill(CODER_CONTRACT, files=", ".join(plan["files_allowed"]),
                 plan=json.dumps(plan, indent=2, ensure_ascii=False))
    return text + (f"\nTHE LAST ATTEMPT FAILED. Fix these, and change nothing else:\n{feedback}\n"
                   if feedback else "")


def coder_argv(wt, model: str, last_path) -> list:
    """``codex exec`` on the ChatGPT subscription, sandboxed to the worktree with no network.

    Every ``-c`` sits after ``exec``: Codex drops root-level overrides once the
    subcommand carries its own (rules/gpt-on-the-subscription-never-credits.md).
    The prompt arrives on stdin, never in argv.
    """
    return ["codex", "exec", "--cd", str(wt), "--skip-git-repo-check", "--sandbox", "workspace-write",
            "--output-last-message", str(last_path), "-m", model,
            "-c", "model_provider=openai", "-c", "forced_login_method=chatgpt",
            "-c", "sandbox_workspace_write.network_access=false",
            "-c", "sandbox_workspace_write.exclude_slash_tmp=true",
            "-c", "sandbox_workspace_write.exclude_tmpdir_env_var=true"]


_ENV_KEEP = ("PATH", "HOME", "LANG", "LC_ALL", "TERM", "USER", "LOGNAME", "SHELL")


def coder_env(wt, home) -> dict:
    """An allowlisted environment: both API-key variables blank, a subscription home."""
    env = {key: os.environ[key] for key in _ENV_KEEP if key in os.environ}
    env.update({"CODEX_HOME": str(home), "OPENAI_API_KEY": "", "CODEX_API_KEY": "",
                "TMPDIR": str(Path(wt) / chk.TMP_DIRNAME)})
    return env


def _header(text: str, key: str) -> str:
    found = re.search(rf"^{key}:\s*(\S+)", text, re.M)
    return found.group(1) if found else ""


def _read(path: Path, limit: int | None = None) -> str:
    return path.read_text(encoding="utf-8", errors="replace")[:limit] if path.exists() else ""


def _live_code(r: Run, seat: Seat, prompt: str, attempt: int) -> CoderResult:
    from lib import openai_billing

    last, out = r.dir / f"coder-last-{attempt}.txt", r.dir / f"coder-output-{attempt}.txt"
    argv, env = coder_argv(r.wt, seat.model, last), coder_env(r.wt, r.home[1])
    try:
        openai_billing.check(argv, env=env)
    except openai_billing.BillingRefused as exc:
        raise Halt("REFUSED", f"the Codex call would not bill the ChatGPT subscription: {exc}") from exc
    prompt_path = r.dir / f"coder-prompt-{attempt}.txt"
    prompt_path.write_text(prompt, encoding="utf-8")
    finished = chk.supervise(argv, env=env, cwd=r.wt, out_path=out, stdin_path=prompt_path,
                             timeout=r.deps.roles.step_seconds["run"],
                             should_stop=lambda: _stop_why(r), poll=r.deps.poll)
    head = _read(out, 4000)
    return CoderResult(finished, _read(last), _header(head, "provider"), _header(head, "model") or seat.model)


def _coder_halt(r: Run, result: CoderResult) -> None:
    finished = result.finished
    if finished.reason not in ("exit", "timeout"):
        raise Halt("STOPPED", finished.reason)
    if result.question:
        _post(r, "ask", f"the coder is blocked and asks: {result.question}")
        raise Halt("BLOCKED", result.question)
    if not finished.passed:
        raise Halt("FAIL", f"the coder ended with {finished.reason} (exit {finished.rc})")
    if result.provider and result.provider != "openai":
        raise Halt("REFUSED", f"the coder ran on provider {result.provider!r}, not the ChatGPT subscription")


def _code(r: Run, plan: dict, feedback: str, attempt: int) -> CoderResult:
    _checkpoint(r)
    seat = r.roster["coder"]
    prompt = r.deps.shield(coder_prompt(plan, feedback))
    result = r.deps.code(r, seat, prompt, attempt)
    _record(r, f"run-{attempt}", {**result.facts(), "prompt_sha256": sha256_text(prompt)},
            seat, result.served)
    _coder_halt(r, result)
    r.served["coder"] = result.served
    return result


# --- check: no model ----------------------------------------------------------------------------


def _live_tests(r: Run, wt, test_paths, timeout: float) -> chk.Finished:
    from lib._compat import grip_python

    out = r.dir / f"tests-{Path(wt).name}-{len(r.leaves):02d}.txt"
    return chk.supervise(chk.sandboxed_pytest_argv(wt, grip_python(), test_paths),
                         env=chk.sandboxed_pytest_env(wt, r.home[1]), cwd=wt, out_path=out,
                         timeout=timeout, should_stop=lambda: _stop_why(r), poll=r.deps.poll)


def _size_faults(wt: Path, files: list) -> list:
    """Refuse an oversized change before its diff is read into memory."""
    total = sum((wt / f).stat().st_size for f in files if (wt / f).is_file())
    return [] if total <= MAX_CHANGE_BYTES else [
        f"the change is {total} bytes, over the {MAX_CHANGE_BYTES}-byte limit"]


def _diff_faults(r: Run, files: list, allowed: list, attempt: int) -> tuple:
    """Stage the planned changes; (diff, faults) for a credential or a binary file in them."""
    diff = chk.staged_diff(r.wt, [f for f in files if f in set(allowed)])
    hits = chk.secret_hits(diff)
    # Scanned before it is written: a run directory never holds a credential in plain text.
    patch = chk.redacted(diff) if hits else diff
    (r.dir / f"diff-{attempt}.patch").write_text(patch, encoding="utf-8")
    faults = [f"the diff holds {hits} credential-shaped string(s)"] if hits else []
    faults += [f"a binary file changed, which the secrets scan cannot read: {f}"
               for f in chk.binary_files(r.wt)]
    return diff, faults, {"patch": f"diff-{attempt}.patch", "patch_sha256": sha256_text(patch)}


def _check(r: Run, plan: dict, attempt: int) -> dict:
    _checkpoint(r)
    files = chk.changed_files(r.wt)
    faults = (chk.scope_faults(files, plan["files_allowed"], r.forbidden) + _size_faults(r.wt, files)
              + chk.symlink_faults(r.wt, files))
    diff, more, patch = ("", [], {}) if faults else _diff_faults(r, files, plan["files_allowed"], attempt)
    faults += more
    budget = r.deps.roles.step_seconds["check"] / 2
    support = [f for f in files if chk.is_test_side(f) and f not in plan["test_paths"]]
    tests = None if faults else chk.red_green(
        r.req.repo, r.base_commit, r.wt, plan["test_paths"], support,
        lambda wt: r.deps.tests(r, wt, plan["test_paths"], budget))
    _checkpoint(r)  # a stop or veto that ended a test run ends the run, never the repair round
    ok = not faults and bool(tests and tests["ok"])
    r.tested_tree = tests["tree"] if ok else ""
    art = {"changed": files, "faults": faults, "tests": tests, "ok": ok, **patch,
           "diff_sha256": sha256_text(diff), "diff_bytes": len(diff.encode("utf-8"))}
    _record(r, f"check-{attempt}", art)
    return {**art, "diff": diff}


# --- review: a third vendor, quote-gated ---------------------------------------------------------

REVIEW_CONTRACT = """You are the reviewer seat of /connect. You cannot run tools. Review the diff
below against the task and the plan. The change already passed a check with no model: only the
planned files changed, no credential was found, and its tests failed before and pass after.

Return ONE JSON object and nothing else:
{"verdict": "PASS" or "CHANGES",
 "concerns": [{"file": "path", "quote": "text copied exactly from one added or removed line",
               "why": "what is wrong", "blocking": true}]}

A concern blocks only when its quote is copied exactly from an added or removed line of the
diff. Others are recorded and do not block. Bracketed placeholders such as [PERSON-1] in the
task or the plan are redaction marks added before you saw the text, not defects.

TASK:
{task}

PLAN:
{plan}

CHECK:
{check}

DIFF:
{diff}
"""


def review_prompt(task: str, plan: dict, check: dict) -> str:
    summary = {"changed": check["changed"], "tests": (check.get("tests") or {}).get("why", "")}
    return _fill(REVIEW_CONTRACT, task=task, plan=json.dumps(plan, indent=2, ensure_ascii=False),
                 check=json.dumps(summary, ensure_ascii=False), diff=check["diff"])


def _review_reply_fault(text: str) -> str:
    """A review counts only with a PASS or CHANGES verdict and a concerns list: a
    malformed or cut-off reply must never approve a change by saying nothing."""
    raw = parse_json(text)
    if not isinstance(raw, dict) or raw.get("verdict") not in ("PASS", "CHANGES"):
        return "the reply holds no PASS or CHANGES verdict"
    return "" if isinstance(raw.get("concerns"), list) else "the reply's concerns are not a list"


def _concern(item: dict) -> dict:
    return {"file": str(item.get("file", ""))[:300], "quote": str(item.get("quote", ""))[:2000],
            "why": str(item.get("why", ""))[:2000], "blocking": item.get("blocking") is True}


def _quoted(quote: str, text: str) -> bool:
    quote = quote.strip()
    return len(quote) >= 6 and quote in text


def _review(r: Run, plan: dict, check: dict, attempt: int) -> dict:
    _checkpoint(r)
    prompt = review_prompt(r.req.task, plan, check)
    seat, reply = _call_role(r, "reviewer", prompt, r.deps.roles.step_seconds["review"], _review_reply_fault)
    raw = parse_json(reply.text) or {}
    concerns = [_concern(c) for c in (raw.get("concerns") or [])[:20] if isinstance(c, dict)]
    # The reviewer saw the diff after the shield, so a quote of a redacted line is
    # matched against that view as well as against the raw diff.
    seen = reply.sent.split("\nDIFF:\n", 1)[-1]
    text = chk.quotable(check["diff"]) + "\n" + chk.quotable(seen)
    blocking = [c for c in concerns if c["blocking"] and _quoted(c["quote"], text)]
    art = {"verdict": str(raw.get("verdict", ""))[:20], "concerns": concerns, "blocking": blocking,
           **reply.facts()}
    _record(r, f"review-{attempt}", art, seat, reply.served)
    return art


# --- the loop, the commit and the close ----------------------------------------------------------


def _feedback(check: dict, review) -> str:
    lines = [f"- {fault}" for fault in check["faults"]]
    tests = check.get("tests")
    if tests and not tests["ok"]:
        lines.append(f"- tests: {tests['why']}\n{tests.get('output', '')[-2000:]}")
    lines += [f"- reviewer, {c['file']}: {c['why']}\n  quoted: {c['quote'][:300]}"
              for c in (review or {}).get("blocking", [])]
    return "\n".join(lines)


def _attempts(r: Run, plan: dict) -> dict:
    """Run, check and review; one repair round after a failed check or a blocking concern."""
    feedback = ""
    rounds = 1 + r.deps.roles.repair_rounds
    for attempt in range(rounds):
        _code(r, plan, feedback, attempt)
        check = _check(r, plan, attempt)
        review = _review(r, plan, check, attempt) if check["ok"] else None
        if review is not None and not review["blocking"]:
            _checkpoint(r)  # a stop or veto that arrived during the review still wins
            return {"outcome": "PASS", "attempts": attempt + 1}
        feedback = _feedback(check, review)
    return {"outcome": "FAIL", "attempts": rounds,
            "why": "the change still failed its check or review after the repair round"}


def _commit_message(r: Run, plan: dict) -> str:
    def line(role: str) -> str:
        seat = r.roster[role]
        return f"{r.served.get(role, seat.model)} ({seat.vendor}, {seat.billing})"

    return "\n".join([
        f"connect: {textwrap.shorten(plan['goal'], width=64, placeholder='...')}", "",
        f"Task: {' '.join(r.req.task.split())[:400]}", "",
        f"Planned-By: {line('planner')}", f"Coded-By: {line('coder')}",
        f"Reviewed-By: {line('reviewer')}", f"Connect-Run: {r.id}",
        f"Connect-Last-Leaf: {r.leaves[-1]['id']}", ""])


def _commit(r: Run, plan: dict) -> dict:
    """Commit the tested tree on a local branch. Hooks are off (chk.git) and --no-verify
    is set as well: the conductor runs git outside the sandbox, so no hook may run."""
    _checkpoint(r)
    branch = f"connect/{r.id}"
    try:
        chk.git(r.wt, "switch", "-q", "-c", branch)
        chk.git(r.wt, "commit", "-q", "--no-verify", "-F", "-", stdin=_commit_message(r, plan), timeout=300)
    except chk.CheckError as exc:
        return {"outcome": "FAIL", "why": f"the commit was refused: {exc}"}
    tree = chk.git(r.wt, "rev-parse", "HEAD^{tree}").strip()
    if tree != r.tested_tree:
        return {"outcome": "FAIL", "branch": branch,
                "why": f"the committed tree {tree[:12]} is not the tree the tests passed on"}
    return {"branch": branch, "commit": chk.git(r.wt, "rev-parse", "HEAD").strip()}


def _run_seal(r: Run) -> str:
    chain = [[leaf["step"], leaf["sha256"], leaf["id"]] for leaf in r.leaves]
    return sha256_text(json.dumps(chain, separators=(",", ":")))


def _close(r: Run, result: dict) -> dict:
    keep = result["outcome"] not in ("PASS", "PLANNED", "REFUSED")
    art = {**result, "run_seal": _run_seal(r), "reserved_micro_usd": r.budget.reserved_micro,
           "served": dict(r.served), "worktree_kept": keep}
    _record(r, "close", art)
    _post(r, "verdict", f"{result['outcome']}: {result.get('why') or result.get('branch', '')}"
                        f" (seal {art['run_seal'][:16]})")
    if not keep:
        chk.remove_worktree(r.req.repo, r.wt)
    state = {**art, "run": r.id, "room": r.room, "leaves": r.leaves, "worktree": str(r.wt),
             "task_sha256": r.task_sha, "closed": int(time.time())}
    (r.dir / "state.json").write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {**state, "dir": str(r.dir)}


def _drive(r: Run) -> dict:
    try:
        plan = _plan(r)
        result = {"outcome": "PLANNED"} if r.req.plan_only else _attempts(r, plan)
        if result["outcome"] == "PASS":
            result.update(_commit(r, plan))
    except Halt as halt:
        result = {"outcome": halt.outcome, "why": str(halt)}
    except chk.CheckError as exc:
        result = {"outcome": "FAIL", "why": f"a git step failed: {exc}"}
    return _close(r, result)


def run(req: Request, deps: Deps) -> dict:
    """Admit, plan, run, check, review and close one /connect run. Never pushes."""
    r = admit(req, deps)
    try:
        return _drive(r)
    finally:
        r.lock.release()


# --- verify -----------------------------------------------------------------------------------------


def _read_jsonl(path: Path) -> list:
    return [json.loads(line) for line in _read(path).splitlines() if line.strip()]


def _leaf_problems(i: int, path: Path, leaf: dict, prev_id, signature) -> list:
    out = []
    if sha256_bytes(path.read_bytes()) != (leaf.get("decision") or {}).get("artefact_sha256"):
        out.append(f"{path.name}: does not match the hash its leaf signed")
    if leaf.get("predecessor_idr") != prev_id or leaf.get("depth") != i:
        out.append(f"{path.name}: its leaf is not linked to the one before")
    verdict = signature(leaf) if signature else "VERIFIED"
    if verdict != "VERIFIED":
        out.append(f"{path.name}: its leaf's signature is {verdict}, not VERIFIED")
    return out


def _patch_problems(run_dir: Path, files: list) -> list:
    """Each check's stored diff must be the one its signed artefact names."""
    checks = [json.loads(path.read_text(encoding="utf-8")) for path in files if "-check-" in path.name]
    named = [c for c in checks if c.get("patch")]
    return [f"{c['patch']}: missing, or not the diff its check signed" for c in named
            if not (run_dir / c["patch"]).is_file()
            or sha256_bytes((run_dir / c["patch"]).read_bytes()) != c.get("patch_sha256")]


def _terminal_problems(leaves: list) -> list:
    last = (leaves[-1].get("decision") or {}).get("step") if leaves else ""
    return [] if last == "close" else [f"the record ends at {last or 'nothing'}, not at a signed close"]


def _chain_problems(run_id: str, leaves: list, chain) -> list:
    """The shared chain is append-only: a suffix cut from the local record still shows there."""
    on_chain = [leaf.get("id") for leaf in chain(run_id)]
    local = [leaf.get("id") for leaf in leaves]
    if on_chain == local:
        return []
    return [f"the shared chain holds {len(on_chain)} leaves for this run and the local record "
            f"{len(local)}, or their ids differ"]


def verify_run(run_dir, signature=None, chain=None) -> list:
    """Every problem with a run's record: an edited artefact, a broken link, a bad
    signature, a record that does not end at a signed close, or (given ``chain``,
    the shared chain's leaves for a run id) a record that differs from the chain."""
    run_dir = Path(run_dir)
    leaves = _read_jsonl(run_dir / "leaves.jsonl")
    files = sorted(run_dir.glob("[0-9][0-9]-*.json"))
    problems = [] if len(files) == len(leaves) and leaves else [f"{len(files)} artefacts, {len(leaves)} leaves"]
    for i, (path, leaf) in enumerate(zip(files, leaves)):
        problems += _leaf_problems(i, path, leaf, leaves[i - 1]["id"] if i else None, signature)
    problems += _terminal_problems(leaves) + _patch_problems(run_dir, files)
    return problems + (_chain_problems(run_dir.name, leaves, chain) if chain else [])


# --- live wiring ----------------------------------------------------------------------------------


def _restore_env(key: str, saved) -> None:
    if saved is None:
        os.environ.pop(key, None)
    else:
        os.environ[key] = saved


def _council_call(seat: Seat, prompt: str, timeout: int) -> tuple:
    """A metered seat through sovereign_council.provider_call, with the seat's own output cap.

    GRIP_COUNCIL_MODEL_<SEAT> is that module's documented way to send another
    model at the same endpoint and key, set for this one call only.
    """
    from lib import sovereign_council as sc

    key = "GRIP_COUNCIL_MODEL_" + sc._seat_env_key(seat.target)
    saved = os.environ.get(key)
    os.environ[key] = seat.model
    try:
        res = sc.provider_call(seat.target, [{"role": "user", "content": prompt}], time.time(),
                               max_tokens=seat.max_out, timeout=timeout)
    finally:
        _restore_env(key, saved)
    text = str(res.get("text") or "")
    if not res.get("ok") or text.startswith("(200 but empty") or res.get("truncated"):
        raise RuntimeError("answer cut off at its output cap" if res.get("truncated") else text[:300] or "no answer")
    return text, str(res.get("model") or seat.model)


def _live_call(seat: Seat, prompt: str, timeout: int) -> tuple:
    if seat.transport == "claude-p":
        from lib.ultrathink_trio import LIVE_TRANSPORTS, Rung

        return LIVE_TRANSPORTS["claude-p"](Rung(seat.id, seat.model, "claude-p"), prompt)
    if seat.transport == "council":
        return _council_call(seat, prompt, timeout)
    raise RuntimeError(f"no text transport for {seat.transport!r}")


def _live_seal(decision: dict, inputs: dict, predecessor, depth: int) -> dict:
    from lib.precog.idr import append_idr, build_idr

    idr = build_idr(prompt=f"connect:{decision['run']}:{decision['step']}", fingerprint="grip-connect",
                    decision=decision, predecessor_idr=predecessor, depth=depth, kind="connect-step",
                    inputs=inputs)
    append_idr(idr)
    body = asdict(idr)
    if body.get("decision_anatomy") is None:
        body.pop("decision_anatomy", None)
    return body


def _live_signature(leaf: dict) -> str:
    from lib.idr_forest import _verify_node
    from lib.precog.idr import PrecogIDR

    try:
        return _verify_node(PrecogIDR(**{"decision_anatomy": None, **leaf})).name
    except Exception as exc:  # noqa: BLE001 -- a signature nobody could check is not clean
        return f"UNVERIFIABLE ({type(exc).__name__})"


def _live_chain(run_id: str) -> list:
    """This run's leaves on the shared IDR chain, in the order they were appended."""
    from lib.precog.idr import _default_idr_path

    with open(_default_idr_path(), encoding="utf-8") as fh:
        rows = [json.loads(line) for line in fh if run_id in line]
    return [row for row in rows
            if row.get("kind") == "connect-step" and (row.get("decision") or {}).get("run") == run_id]


def _live_shield(text: str) -> str:
    try:
        from lib.review_redaction import shield_payload

        return shield_payload(text)
    except Exception as exc:  # noqa: BLE001 -- nothing is sent when the shield fails
        raise Halt("FAIL", f"the redaction shield failed, so nothing was sent ({type(exc).__name__})") from exc


def _home_candidates(homes: list) -> list:
    """(index, home) pairs the coder may use: a pinned one, or every home but the reserved ones.

    The first home is the council's own Codex seat (lib/sovereign_council.py),
    so it is never a coder. GRIP_CONNECT_RESERVED_HOMES lists more, such as
    the PR-review seat; GRIP_CONNECT_CODEX_HOME pins one.
    """
    pinned = os.environ.get("GRIP_CONNECT_CODEX_HOME", "").strip()
    if pinned:
        target = Path(pinned).expanduser().resolve()
        return [(i, h) for i, h in enumerate(homes) if h == target]
    reserved_raw = os.environ.get("GRIP_CONNECT_RESERVED_HOMES", "").split(os.pathsep)
    reserved = {Path(p).expanduser().resolve() for p in reserved_raw if p.strip()}
    return [(i, h) for i, h in enumerate(homes) if i > 0 and h not in reserved]


def _home_ready(home: Path) -> bool:
    """Ready means both windows have room and the home holds no credits Codex could fall to."""
    from lib import subscription_resets as sr

    try:
        facts = sr.parse_rate_limits(sr.fetch_rate_limits(home))
    except Exception:  # noqa: BLE001 -- a home that cannot be read is not used
        return False
    return facts["state"] == "ready" and not facts["has_credits"]


def _ready_home():
    from lib import openai_billing

    for index, home in _home_candidates(list(openai_billing.subscription_homes())):
        if _home_ready(home):
            return index, home
    return None


def live_deps(roles_path=None) -> Deps:
    from lib.claude_dir import get_claude_dir

    worktrees = os.environ.get("GRIP_CONNECT_WORKTREES") or str(Path.home() / ".grip-worktrees")
    return Deps(roles=load_roles(roles_path), state_dir=get_claude_dir() / "state" / "connect",
                worktrees_dir=Path(worktrees), call=_live_call, code=_live_code, tests=_live_tests,
                seal=_live_seal, home=_ready_home, shield=_live_shield)


# --- command line ------------------------------------------------------------------------------------


def _request(args) -> Request:
    roots = tuple(r if r.endswith("/") else r + "/" for r in args.roots.split(",") if r.strip())
    return Request(task=" ".join(args.task), repo=Path(args.repo).resolve(), base=args.base,
                   files=tuple(args.file), roots=roots, plan_only=args.cmd == "plan",
                   accept_open_reads=args.accept_open_reads)


def _print_result(res: dict) -> None:
    print(f"/connect run {res['run']}: {res['outcome']}")
    for label, key in (("why", "why"), ("branch", "branch"), ("commit", "commit"), ("run seal", "run_seal")):
        if res.get(key):
            print(f"  {label}: {res[key]}")
    print(f"  served: {json.dumps(res.get('served', {}))}")
    print(f"  reserved: {res.get('reserved_micro_usd', 0) / 1e6:.4f} USD of metered calls (worst case)")
    print(f"  room: bin/mesh room --room {res['room']} tail 50")
    print(f"  verify: bin/grip-connect verify {res['run']}")
    if res.get("worktree_kept"):
        print(f"  worktree kept for inspection: {res['worktree']}")


def _cmd_run(args) -> int:
    try:
        res = run(_request(args), live_deps())
    except ConnectRefused as exc:
        print(f"/connect refused: {exc}", file=sys.stderr)
        return 2
    _print_result(res)
    return 0 if res["outcome"] in ("PASS", "PLANNED") else 1


def _cmd_roster(args) -> int:
    roles = load_roles()
    for triple, found in explain(roles)[:args.limit]:
        names = " + ".join(f"{s.role} {s.id}" for s in triple)
        print(f"{'OK  ' if not found else 'NO  '}{names}" + (f"  -- {'; '.join(found)}" if found else ""))
    return 0


def _cmd_status(args) -> int:
    runs = live_deps().state_dir / "runs"
    dirs = [runs / args.run] if args.run else sorted(runs.glob("*"))[-args.limit:]
    for path in dirs:
        state = json.loads(_read(path / "state.json") or "{}")
        print(f"{path.name}  {state.get('outcome', 'open or crashed')}  {state.get('branch', '')}")
    return 0


def _cmd_stop(args) -> int:
    marker = live_deps().state_dir / (f"STOP-{args.run}" if args.run else "STOP")
    if args.clear:
        marker.unlink(missing_ok=True)
        print(f"cleared {marker}")
        return 0
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(f"set {int(time.time())}\n", encoding="utf-8")
    print(f"set {marker}: a running /connect stops within its poll interval")
    return 0


def _cmd_verify(args) -> int:
    problems = verify_run(live_deps().state_dir / "runs" / args.run, signature=_live_signature,
                          chain=_live_chain)
    print("\n".join(problems) if problems else f"{args.run}: every artefact matches its signed leaf")
    return 1 if problems else 0


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="grip-connect", description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("run", "plan"):
        s = sub.add_parser(name)
        s.add_argument("task", nargs="+")
        s.add_argument("--repo", default=os.getcwd())
        s.add_argument("--base", default="origin/main")
        s.add_argument("--file", action="append", default=[])
        s.add_argument("--roots", default=",".join(DEFAULT_ROOTS))
        s.add_argument("--accept-open-reads", action="store_true",
                       help="run although the coder can read outside its worktree (GRIP#9154)")
    sub.add_parser("roster").add_argument("--limit", type=int, default=8)
    status = sub.add_parser("status")
    status.add_argument("run", nargs="?")
    status.add_argument("--limit", type=int, default=10)
    stop = sub.add_parser("stop")
    stop.add_argument("run", nargs="?")
    stop.add_argument("--clear", action="store_true")
    sub.add_parser("verify").add_argument("run")
    return p


_COMMANDS = {"run": _cmd_run, "plan": _cmd_run, "roster": _cmd_roster, "status": _cmd_status,
             "stop": _cmd_stop, "verify": _cmd_verify}


def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    return _COMMANDS[args.cmd](args)


if __name__ == "__main__":
    # Re-exec under an interpreter that can dual-sign before anything signs, as
    # lib/ultrathink_trio.py does (rules/never-system-python-for-grip.md).
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parent))
    from pq_reexec import reexec_if_incapable as _reexec

    _reexec(_Path(__file__))
    sys.exit(main())
