#!/usr/bin/env python3
"""grip_dirty_snapshot -- the Clean Ledger's writer: which session dirtied what.

Many sessions share one working tree (~/.claude) and, through linked worktrees,
one stash stack. Before this module nothing recorded who left a dirty file or a
stash behind, so leftover churn was either lost or merged over by whoever came
next. This is Pillar 1 of the Clean Ledger: attribution. Pillar 2 (a commit
toll gate) is meant to follow once the ledger has proved itself.

The ledger is one JSON manifest per session, kept where no working tree sees it:

    <git-common-dir>/grip-ledger/<session-id>.json

The common dir is shared by every linked worktree (lib/git_lock.py anchors its
lock there for the same reason), is never tracked, never shows as dirty, and
git-crypt's filters never run on it.

A manifest holds CLAIMS: "this session left <tree>/<path> looking like this",
recorded as (status, size, mtime_ns, sha256). They are written by bin/grip-git
(a before/after snapshot around each working-tree-mutating git op), by
bin/grip-stash, and by the explicit `claim` verb below. bin/grip-ownership reads
every manifest and attributes the live dirty set; lib/grip_ownership.py holds
that logic.

Honest limits, stated here so nothing downstream overclaims:
  * Only work that passes through grip-git, grip-stash, `claim`, or -- once an
    operator binds it -- hooks/ledger-claim-hook.py (which claims each file a
    Write/Edit/MultiEdit/NotebookEdit tool call touches, via claim_paths) is
    attributed. An editor, a shell redirect or bare `git` leaves dirt that
    reads UNATTRIBUTED -- the gap is visible rather than silent, which is the
    point.
  * Claims are cooperative, not proof. Anyone holding a session id can claim
    anything, and a foreign edit landing during a long grip-git op is claimed
    by that op.
  * No liveness: a dead session's claims persist until the path is clean.

Usage:
    grip_dirty_snapshot.py claim   [--tree PATH] PATH...
    grip_dirty_snapshot.py release [--tree PATH] PATH...
    grip_dirty_snapshot.py show    [--tree PATH] [--json]

Exit codes: 0 done, 2 missing/invalid session id or bad usage, 3 git or ledger failure.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterator, Optional, Sequence

_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))
from atomic_write import atomic_write  # noqa: E402

SCHEMA = 1
LEDGER_DIRNAME = "grip-ledger"
OPS_CAP = 200
PATHS_PER_OP_CAP = 200
LOCK_WAIT_S = 2.0
LOCK_POLL_S = 0.01
LOCK_STALE_S = 10.0
SESSION_ENV_ORDER = ("GRIP_SESSION_ID", "CLAUDE_CODE_SESSION_ID", "CLAUDE_SESSION_ID")
STASH_PREFIX = "grip-stash"
UNLABELLED = "unlabelled"

_SID_PATTERN = r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}"
_SID_RE = re.compile(rf"^{_SID_PATTERN}$")
# git refnames cannot contain ':', so the branch field is exact; the reason
# takes the rest of the subject and may contain colons of its own.
STASH_RE = re.compile(
    rf"{STASH_PREFIX}:(?P<sid>{_SID_PATTERN}):(?P<branch>[^:]*):(?P<reason>.*)"
)
# Global options that take their value as a separate token (`git -C dir add`).
_GLOBAL_WITH_VALUE = frozenset(
    {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--config-env"}
)
_STATUS_ARGS = ("status", "--porcelain=v2", "-z", "--no-renames",
                "--ignore-submodules=dirty")
# Porcelain v2 record type -> number of space-separated fields before the path.
_FIELDS_BEFORE_PATH = {"1": 8, "2": 9, "u": 10}
_HASH_CHUNK = 1 << 20
#: Files larger than this record 'too-large:<size>' instead of a digest; writer
#: and reader cap identically, so a hook never spends its deadline hashing.
HASH_CAP_BYTES = 64 << 20
#: Per-call git bound for claim_paths(), inside the Write/Edit hook's deadline.
CLAIM_GIT_TIMEOUT_S = 3.0
# Variables that point git at a repository other than the one a path lives in.
_REPO_REDIRECTS = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR")


class LedgerError(Exception):
    """A ledger operation that could not complete (git failed, file unreadable)."""


class InvalidSessionId(LedgerError):
    """A session-id variable is set but is not a safe identifier."""


class CorruptManifest(LedgerError):
    """A manifest exists but is not a readable schema-1 ledger."""


class LedgerBusy(LedgerError):
    """The manifest lock stayed held past LOCK_WAIT_S."""


# ---------------------------------------------------------------------------
# session identity
# ---------------------------------------------------------------------------

def valid_session_id(value: Optional[str]) -> bool:
    """True when `value` is safe as a manifest filename and a stash field."""
    return bool(_SID_RE.match(value or ""))


def resolve_session_id(env: Optional[dict] = None) -> Optional[str]:
    """The owning session's id, or None when no harness has set one.

    GRIP_SESSION_ID wins because it is the only variable a Codex or OpenCode
    user can set; CLAUDE_CODE_SESSION_ID is the one Claude Code sets;
    CLAUDE_SESSION_ID is kept because lib/git_lock.py still reads it. A value
    that is set but unsafe raises instead of falling through: it becomes a
    filename, and a silently skipped override would attribute work to the
    wrong session.
    """
    source = os.environ if env is None else env
    for name in SESSION_ENV_ORDER:
        value = (source.get(name) or "").strip()
        if not value:
            continue
        if not valid_session_id(value):
            raise InvalidSessionId(
                f"{name}={value!r} is not a safe session id "
                "(1-128 chars of [A-Za-z0-9._-], starting alphanumeric)"
            )
        return value
    return None


def _agent(env: Optional[dict]) -> str:
    source = os.environ if env is None else env
    explicit = (source.get("GRIP_AGENT") or "").strip()
    if explicit:
        return explicit
    return "claude-code" if source.get("CLAUDE_CODE_SESSION_ID") else "unknown"


# ---------------------------------------------------------------------------
# reaching git
# ---------------------------------------------------------------------------

def real_git(env: Optional[dict] = None, self_path: Optional[Path] = None) -> str:
    """The first `git` on PATH that is not this wrapper (GRIP_GIT_REAL overrides).

    The intended deployment is `alias git=grip-git`; a PATH shim named git
    pointing at the wrapper would otherwise make it call itself.
    """
    source = os.environ if env is None else env
    override = (source.get("GRIP_GIT_REAL") or "").strip()
    if override:
        return override
    name = "git.exe" if os.name == "nt" else "git"
    for folder in (source.get("PATH") or "").split(os.pathsep):
        candidate = Path(folder or ".") / name
        if _is_runnable(candidate) and not _same_file(candidate, self_path):
            return str(candidate)
    return "git"


def _is_runnable(path: Path) -> bool:
    return path.is_file() and os.access(path, os.X_OK)


def _same_file(a: Path, b: Optional[Path]) -> bool:
    if b is None:
        return False
    try:
        return os.path.samefile(a, b)
    except OSError:
        return False


def split_global_options(argv: Sequence[str]) -> tuple[list[str], Optional[str], list[str]]:
    """Split `git [global options] <sub> [args]` into its three parts.

    Returns (argv, None, []) when there is no subcommand -- `git --version`, or
    a value-taking option whose value is missing.
    """
    args = list(argv)
    i = 0
    while i < len(args):
        token = args[i]
        if token in _GLOBAL_WITH_VALUE:
            i += 2
        elif token.startswith("-"):
            i += 1
        else:
            return args[:i], token, args[i + 1:]
    return args, None, []


class GitCtx:
    """How to reach one repository: the git argv prefix plus a working directory.

    `argv0` carries the caller's own global options (-C, --git-dir,
    --work-tree), so every probe here addresses exactly the repository the
    caller's command addresses -- git resolves the paths, not this module.
    Probes add --no-optional-locks: a ledger that runs `status` around every
    op must never contend with another session for index.lock.
    """

    def __init__(self, argv0: Sequence[str], cwd: str, timeout: float = 120.0,
                 env: Optional[dict] = None) -> None:
        self.argv0 = list(argv0)
        self.cwd = cwd
        self.timeout = timeout
        self.env = env

    def run(self, *args: str, check: bool = True) -> str:
        try:
            proc = subprocess.run(
                [*self.argv0, "--no-optional-locks", *args], cwd=self.cwd,
                capture_output=True, encoding="utf-8", errors="surrogateescape",
                timeout=self.timeout, env=self.env,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise LedgerError(f"git {args[0] if args else ''}: {exc}") from exc
        if check and proc.returncode != 0:
            detail = (proc.stderr or "").strip().splitlines()
            raise LedgerError(
                f"git {' '.join(args[:2])} failed (exit {proc.returncode})"
                + (f": {detail[0]}" if detail else "")
            )
        return proc.stdout

    def toplevel(self) -> str:
        return self.run("rev-parse", "--show-toplevel").strip()

    def ledger_dir(self) -> Path:
        common = self.run("rev-parse", "--path-format=absolute",
                          "--git-common-dir").strip()
        return Path(common) / LEDGER_DIRNAME

    def branch_label(self) -> str:
        """Current branch, or detached-<sha7>; never contains ':'."""
        name = self.run("symbolic-ref", "--short", "-q", "HEAD", check=False).strip()
        if name:
            return name
        head = self.run("rev-parse", "--short=7", "HEAD", check=False).strip()
        return f"detached-{head or 'unborn'}"

    def head(self) -> Optional[str]:
        return self.run("rev-parse", "HEAD", check=False).strip() or None


def ctx_for(tree: Optional[str], git: Optional[str] = None) -> GitCtx:
    """A context for `tree` (via -C), or for the current directory."""
    argv0 = [git or real_git()]
    if tree:
        argv0 += ["-C", tree]
    return GitCtx(argv0, os.getcwd())


# ---------------------------------------------------------------------------
# the dirty set
# ---------------------------------------------------------------------------

def parse_status_v2(raw: str) -> dict[str, str]:
    """relpath -> XY for each record of `git status --porcelain=v2 -z`.

    Untracked files read as '??'. Rename records (type 2) are parsed even
    though --no-renames suppresses them, so a caller that drops the flag
    loses no entries: the original path follows as its own NUL-terminated
    token and is skipped.
    """
    entries: dict[str, str] = {}
    tokens = raw.split("\0")
    i = 0
    while i < len(tokens):
        token = tokens[i]
        i += 1
        kind = token[:1]
        if kind == "?":
            entries[token[2:]] = "??"
        elif kind in _FIELDS_BEFORE_PATH:
            parts = token.split(" ", _FIELDS_BEFORE_PATH[kind])
            if len(parts) == _FIELDS_BEFORE_PATH[kind] + 1:
                entries[parts[-1]] = parts[1]
            i += 1 if kind == "2" else 0
    return entries


def dirty_status(ctx: GitCtx, untracked: str = "all") -> dict[str, str]:
    """Every dirty path in the context's tree: staged, unstaged, untracked,
    deleted and unmerged. -uall lists untracked files one by one, because a
    claim needs a per-file fingerprint."""
    return parse_status_v2(ctx.run(*_STATUS_ARGS, f"--untracked-files={untracked}"))


def fingerprint(tree: str, rel: str) -> tuple[Optional[int], Optional[int]]:
    """(size, mtime_ns) from one lstat; (None, None) when the path is gone."""
    try:
        st = os.lstat(os.path.join(tree, rel))
    except OSError:
        return (None, None)
    return (st.st_size, st.st_mtime_ns)


def snapshot(ctx: GitCtx, tree: str) -> dict[str, tuple]:
    """relpath -> (XY, size, mtime_ns) for every dirty path.

    Hashing is deliberately absent. The shared tree carries ~5,400 dirty
    entries (measured 2026-09-29); one status plus one lstat each is enough
    to tell which of them an op touched, and only those get hashed.
    """
    return {rel: (xy, *fingerprint(tree, rel))
            for rel, xy in dirty_status(ctx).items()}


def diff_snapshots(before: dict, after: dict) -> tuple[list[str], list[str]]:
    """(paths an op made dirty or changed, paths an op made clean)."""
    changed = sorted(rel for rel, state in after.items() if before.get(rel) != state)
    released = sorted(rel for rel in before if rel not in after)
    return changed, released


def content_hash(tree: str, rel: str) -> Optional[str]:
    """sha256 of the working-tree bytes; None when the path is gone.

    A symlink hashes its target string, a directory (a submodule) records its
    HEAD, a file over HASH_CAP_BYTES records 'too-large:<size>', and an
    unreadable file reads 'unreadable' -- each distinct from None, which must
    keep meaning "deleted". The cap means a same-size change to a huge file
    reads unchanged: a stated limit, traded for a bounded hook.
    """
    full = os.path.join(tree, rel)
    try:
        if os.path.islink(full):
            return "link:" + hashlib.sha256(os.fsencode(os.readlink(full))).hexdigest()
        if os.path.isdir(full):
            return "sub:" + _submodule_head(full)
        size = os.path.getsize(full)
        if size > HASH_CAP_BYTES:
            return f"too-large:{size}"
        digest = hashlib.sha256()
        with open(full, "rb") as handle:
            for chunk in iter(lambda: handle.read(_HASH_CHUNK), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except FileNotFoundError:
        return None
    except OSError:
        return "unreadable"


def _submodule_head(path: str) -> str:
    try:
        proc = subprocess.run([real_git(), "-C", path, "rev-parse", "HEAD"],
                              capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return "unknown"
    return proc.stdout.strip() or "unknown"


# ---------------------------------------------------------------------------
# manifests
# ---------------------------------------------------------------------------

def utc_now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def manifest_path(ledger: Path, sid: str) -> Path:
    return ledger / f"{sid}.json"


def new_manifest(sid: str, env: Optional[dict] = None) -> dict:
    now = utc_now()
    return {"schema": SCHEMA, "session_id": sid, "agent": _agent(env),
            "created_at": now, "updated_at": now,
            "claims": {}, "stashes": {}, "ops": []}


def load_manifest(path: Path) -> Optional[dict]:
    """The manifest at `path`; None when absent; CorruptManifest when unreadable.

    Corruption raises rather than reading as "no claims": a manifest that
    cannot be parsed would otherwise turn every path it owns into a false
    UNATTRIBUTED -- the one verdict the ledger exists to make trustworthy.
    """
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError) as exc:
        raise CorruptManifest(f"{path}: unreadable ({exc})") from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise CorruptManifest(f"{path}: not JSON ({exc.msg}, line {exc.lineno})") from exc
    _check_shape(path, data)
    return data


def _check_shape(path: Path, data: object) -> None:
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        raise CorruptManifest(f"{path}: not a schema-{SCHEMA} ledger manifest")
    if data.get("session_id") != path.stem:
        raise CorruptManifest(
            f"{path}: session_id {data.get('session_id')!r} does not match its filename"
        )
    for key, kind in (("claims", dict), ("stashes", dict), ("ops", list)):
        if not isinstance(data.get(key), kind):
            raise CorruptManifest(f"{path}: '{key}' is not a {kind.__name__}")


def save_manifest(path: Path, data: dict) -> None:
    """Atomic write (temp file + os.replace), retried on Windows sharing errors."""
    # ensure_ascii: git hands back raw bytes, decoded with surrogateescape,
    # and a lone surrogate cannot be encoded as UTF-8 -- escaped, it round-trips.
    text = json.dumps(data, indent=1, sort_keys=True, ensure_ascii=True) + "\n"
    for attempt in range(3):
        try:
            atomic_write(path, text)
            return
        except PermissionError:
            if attempt == 2:
                raise
            time.sleep(0.05)


@contextmanager
def manifest_lock(path: Path, wait: float = LOCK_WAIT_S,
                  stale: float = LOCK_STALE_S) -> Iterator[None]:
    """Serialise read-modify-write of one manifest, portably.

    An O_CREAT|O_EXCL lock file stops two parallel tool calls in one session
    from dropping each other's claims, and it is atomic everywhere. It was
    chosen while the flock in lib/_compat.py did nothing on Windows, before
    GRIP#8759.
    A lock older than `stale` seconds belongs to a crashed writer and is
    broken; a live one held past `wait` raises LedgerBusy.
    """
    lock = path.with_name(path.name + ".lock")
    lock.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + wait
    while not _try_create(lock):
        if _is_stale(lock, stale):
            _unlink_quiet(lock)
        elif time.monotonic() >= deadline:
            raise LedgerBusy(f"{lock} held for more than {wait:g}s")
        else:
            time.sleep(LOCK_POLL_S)
    try:
        yield
    finally:
        _unlink_quiet(lock)


def _try_create(lock: Path) -> bool:
    try:
        fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except (FileExistsError, PermissionError):
        return False
    os.close(fd)
    return True


def _is_stale(lock: Path, stale: float) -> bool:
    try:
        return time.time() - lock.stat().st_mtime > stale
    except OSError:
        return False


def _unlink_quiet(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        return


def update_manifest(ledger: Path, sid: str, mutate: Callable[[dict], None],
                    env: Optional[dict] = None) -> dict:
    """Apply `mutate` to this session's manifest under its lock; return it."""
    path = manifest_path(ledger, sid)
    with manifest_lock(path):
        data = load_manifest(path) or new_manifest(sid, env)
        mutate(data)
        data["updated_at"] = utc_now()
        save_manifest(path, data)
    return data


def claim_key(tree: str, rel: str) -> str:
    return f"{tree}/{rel}"


def build_claims(tree: str, snap: dict, rels: Sequence[str], op: str, at: str) -> dict:
    """Claim records for `rels`, hashed before any lock is taken."""
    claims = {}
    for rel in rels:
        xy, size, mtime_ns = snap[rel]
        claims[claim_key(tree, rel)] = {
            "tree": tree, "path": rel, "status": xy, "size": size,
            "mtime_ns": mtime_ns, "sha256": content_hash(tree, rel),
            "op": op, "at": at,
        }
    return claims


def apply_record(data: dict, *, tree: str, claims: dict, released: Sequence[str],
                 op_entry: Optional[dict] = None) -> None:
    """Merge claims, drop claims on paths that went clean, append the op."""
    data["claims"].update(claims)
    for rel in released:
        data["claims"].pop(claim_key(tree, rel), None)
    if op_entry is not None:
        ops = data["ops"]
        ops.append(op_entry)
        del ops[:-OPS_CAP]


def op_entry(at: str, argv: Sequence[str], ctx: GitCtx, tree: str, rc: int,
             claimed: Sequence[str], released: Sequence[str],
             commit: Optional[str] = None) -> dict:
    """One ops-log line; path lists are capped, their true sizes kept."""
    return {"at": at, "argv": list(argv), "cwd": ctx.cwd, "tree": tree, "exit": rc,
            "claimed": list(claimed[:PATHS_PER_OP_CAP]), "claimed_n": len(claimed),
            "released": list(released[:PATHS_PER_OP_CAP]), "released_n": len(released),
            "commit": commit}


# ---------------------------------------------------------------------------
# stashes
# ---------------------------------------------------------------------------

def stash_message(sid: str, branch: str, reason: str) -> str:
    """The attributed stash message: grip-stash:<sid>:<branch>:<reason>.

    The reason is collapsed to one line (git keeps only the subject line) and
    defaults to 'unlabelled'.
    """
    if ":" in branch:
        raise LedgerError(f"branch {branch!r} contains ':' and cannot be encoded")
    clean = " ".join((reason or "").split()) or UNLABELLED
    return f"{STASH_PREFIX}:{sid}:{branch}:{clean}"


def parse_stash_subject(subject: str) -> Optional[dict]:
    """{'sid', 'branch', 'reason'} from a grip-stash subject, else None."""
    match = STASH_RE.search(subject or "")
    return match.groupdict() if match else None


def list_stashes(ctx: GitCtx) -> list[dict]:
    """Every stash as {sha, ref, subject}, newest first.

    Identity is the commit SHA. stash@{n} is kept only for display: it shifts
    whenever any session pushes.
    """
    raw = ctx.run("stash", "list", "-z", "--format=%H%x1f%gd%x1f%gs")
    stashes = []
    for record in raw.split("\0"):
        fields = record.split("\x1f", 2)
        if len(fields) == 3 and fields[0].strip():
            stashes.append({"sha": fields[0].strip(), "ref": fields[1],
                            "subject": fields[2]})
    return stashes


def split_stash_push(args: Sequence[str]) -> tuple[list[str], str, list[str]]:
    """(options, message, pathspecs) from `git stash push` arguments.

    Everything after `--` is a pathspec and is never read as an option; a
    bare word before it is a pathspec too, as git itself treats it.
    """
    options: list[str] = []
    paths: list[str] = []
    message = ""
    i = 0
    while i < len(args):
        token = args[i]
        if token == "--":
            paths.extend(args[i:])
            break
        if token in ("-m", "--message"):
            message = args[i + 1] if i + 1 < len(args) else ""
            i += 2
            continue
        message, is_message = _inline_message(token, message)
        if not is_message:
            (options if token.startswith("-") else paths).append(token)
        i += 1
    return options, message, paths


def _inline_message(token: str, current: str) -> tuple[str, bool]:
    if token.startswith("--message="):
        return token.split("=", 1)[1], True
    if token.startswith("-m") and len(token) > 2:
        return token[2:], True
    return current, False


def rewrite_stash_args(rest: Sequence[str], sid: str, branch: str) -> list[str]:
    """`stash ...` with the attribution message injected into push and save.

    Returns the full ["stash", ...] argument list. A push's -m becomes the
    reason; a save's positional words do; any other stash verb is returned
    unchanged.
    """
    args = list(rest)
    if not args or args[0].startswith("-"):
        verb, tail = "push", args
    else:
        verb, tail = args[0], args[1:]
    if verb == "push":
        options, reason, paths = split_stash_push(tail)
    elif verb == "save":
        options = [a for a in tail if a.startswith("-")]
        reason, paths = " ".join(a for a in tail if not a.startswith("-")), []
    else:
        return ["stash", *args]
    return ["stash", "push", "-m", stash_message(sid, branch, reason), *options, *paths]


# ---------------------------------------------------------------------------
# the stamped op: run git, record what it changed
# ---------------------------------------------------------------------------

def stamped_call(ctx: GitCtx, git_args: Sequence[str], sid: str, *, op: str,
                 warn: Callable[[str], None],
                 call: Callable[..., int] = subprocess.call) -> int:
    """Run `git <git_args>` with GRIP_SESSION_ID exported; record the effect.

    The contract is git's: the return value is always git's own exit code.
    Ledger trouble before the op degrades to a plain, unrecorded run; ledger
    trouble after it prints one line and leaves the result alone. Recording
    happens even when git fails -- a conflicted merge leaves precisely the
    dirt that most needs an owner.
    """
    try:
        tree = ctx.toplevel()
        ledger = ctx.ledger_dir()
        before = snapshot(ctx, tree)
        stashes_before = {s["sha"] for s in list_stashes(ctx)} if op == "stash" else set()
    except LedgerError as exc:
        warn(f"ledger unavailable, op not attributed: {exc}")
        return run_git(ctx, git_args, sid, call, warn)
    rc = run_git(ctx, git_args, sid, call, warn)
    try:
        _record(ctx, tree, ledger, sid, op, git_args, rc, before, stashes_before)
    except (LedgerError, OSError) as exc:
        warn(f"op ran (exit {rc}) but was not recorded: {exc}")
    return rc


def run_git(ctx: GitCtx, git_args: Sequence[str], sid: Optional[str],
            call: Callable[..., int], warn: Callable[[str], None]) -> int:
    """The real git with inherited stdio; 130 on Ctrl-C, 127 when git is missing."""
    env = dict(os.environ)
    if sid:
        env["GRIP_SESSION_ID"] = sid
    try:
        return call([*ctx.argv0, *git_args], cwd=ctx.cwd, env=env)
    except KeyboardInterrupt:
        return 130
    except OSError as exc:
        warn(f"cannot run git: {exc}")
        return 127


def _record(ctx: GitCtx, tree: str, ledger: Path, sid: str, op: str,
            git_args: Sequence[str], rc: int, before: dict,
            stashes_before: set) -> None:
    at = utc_now()
    after = snapshot(ctx, tree)
    changed, released = diff_snapshots(before, after)
    claims = build_claims(tree, after, changed, op, at)
    new_stashes = _new_stashes(ctx, sid, stashes_before, at) if op == "stash" else {}
    commit = ctx.head() if op == "commit" and rc == 0 else None
    entry = op_entry(at, git_args, ctx, tree, rc, changed, released, commit)

    def mutate(data: dict) -> None:
        apply_record(data, tree=tree, claims=claims, released=released, op_entry=entry)
        data["stashes"].update(new_stashes)

    update_manifest(ledger, sid, mutate)


def _new_stashes(ctx: GitCtx, sid: str, before: set, at: str) -> dict:
    """Stashes this op created, found by SHA diff plus our own prefix."""
    found = {}
    for entry in list_stashes(ctx):
        parsed = parse_stash_subject(entry["subject"])
        if entry["sha"] in before or not parsed or parsed["sid"] != sid:
            continue
        found[entry["sha"]] = {"branch": parsed["branch"], "reason": parsed["reason"],
                               "at": at, "dropped_at": None}
    return found


# ---------------------------------------------------------------------------
# claiming exact paths: the entry point for hooks/ledger-claim-hook.py
# ---------------------------------------------------------------------------

def status_for(ctx: GitCtx, rels: Sequence[str]) -> dict[str, str]:
    """dirty_status limited to `rels`, each passed as a literal pathspec.

    Limiting keeps a one-file claim cheap on a tree with thousands of dirty
    entries; :(literal) stops a name like `a[1].txt` from globbing onto
    `a1.txt` and claiming a file nobody touched.
    """
    if not rels:
        return {}
    specs = [f":(literal){rel}" for rel in rels]
    return parse_status_v2(ctx.run(*_STATUS_ARGS, "--untracked-files=all", "--", *specs))


def locate(path: str, git: Optional[str] = None, timeout: float = 120.0,
           env: Optional[dict] = None) -> Optional[tuple[str, str]]:
    """(toplevel, tree-relative path) of the working tree holding `path`.

    None when the path is in no working tree, is inside .git itself, or is the
    tree root. The nearest existing parent is asked, so a path whose directory
    is gone still resolves to its repository.
    """
    parent = os.path.dirname(os.path.abspath(path))
    while not os.path.isdir(parent) and os.path.dirname(parent) != parent:
        parent = os.path.dirname(parent)
    try:
        tree = GitCtx([git or real_git(), "-C", parent], os.getcwd(), timeout, env).toplevel()
    except LedgerError:
        return None
    rel = tree_relative(tree, parent, os.path.abspath(path))
    return (tree, rel) if rel else None


def claim_targets(paths: Sequence[str]) -> list[str]:
    """What a claim should cover: each path, plus a symlink's target (a write
    through the link dirties the target, not the link); directories are
    dropped, since a Write/Edit names a file."""
    targets: list[str] = []
    for path in paths:
        if os.path.isdir(path) and not os.path.islink(path):
            continue
        targets.append(path)
        if os.path.islink(path):
            targets.append(os.path.realpath(path))
    return targets


def claim_paths(paths: Sequence[str], sid: str, op: str,
                git: Optional[str] = None,
                timeout: float = CLAIM_GIT_TIMEOUT_S) -> dict[str, dict]:
    """Claim, or release, exact files for `sid`: the Write/Edit hook's call.

    Each path's repository comes from the path itself, never the cwd, so an
    edit in a linked worktree lands on that worktree's tree. A path that is
    dirty is claimed with a fresh hash; one that is now clean -- edited back
    to HEAD, or gitignored -- releases this session's claim on it. Paths
    outside any repository are skipped. git runs with GIT_DIR and its
    siblings removed, so the path alone decides the repository, and with a
    short per-call timeout so a hook's deadline is never spent waiting on it.
    Returns {tree: {"claimed": [...], "released": [...]}}.
    """
    if not valid_session_id(sid):
        raise InvalidSessionId(f"{sid!r} is not a safe session id")
    git = git or real_git()
    env = {key: value for key, value in os.environ.items() if key not in _REPO_REDIRECTS}
    by_tree: dict[str, set[str]] = {}
    for path in claim_targets(paths):
        found = locate(path, git, timeout, env)
        if found is not None:
            by_tree.setdefault(found[0], set()).add(found[1])
    return {tree: _claim_in_tree(GitCtx([git, "-C", tree], os.getcwd(), timeout, env), tree,
                                 sorted(rels), sid, op)
            for tree, rels in by_tree.items()}


def _claim_in_tree(ctx: GitCtx, tree: str, rels: Sequence[str], sid: str,
                   op: str) -> dict:
    status = status_for(ctx, rels)
    dirty = [rel for rel in rels if rel in status]
    clean = [rel for rel in rels if rel not in status]
    ledger = ctx.ledger_dir()
    if not dirty and not holds_any(ledger, sid, tree, clean):
        return {"claimed": [], "released": []}
    at = utc_now()
    snap = {rel: (status[rel], *fingerprint(tree, rel)) for rel in dirty}
    claims = build_claims(tree, snap, dirty, op, at)
    entry = op_entry(at, [op, *rels], ctx, tree, 0, dirty, clean)
    update_manifest(ledger, sid, lambda data: apply_record(
        data, tree=tree, claims=claims, released=clean, op_entry=entry))
    return {"claimed": dirty, "released": clean}


def holds_any(ledger: Path, sid: str, tree: str, rels: Sequence[str]) -> bool:
    """Does this session hold a claim on any of `rels`? A lock-free peek, so an
    edit to an ignored or already-clean file costs no manifest write at all."""
    data = load_manifest(manifest_path(ledger, sid))
    return bool(data) and any(claim_key(tree, rel) in data["claims"] for rel in rels)


# ---------------------------------------------------------------------------
# CLI: claim / release / show
# ---------------------------------------------------------------------------

def _say(message: str) -> None:
    print(f"[grip-dirty-snapshot] {message}", file=sys.stderr)


def _require_sid() -> str:
    try:
        sid = resolve_session_id()
    except InvalidSessionId as exc:
        _say(str(exc))
        raise SystemExit(2) from exc
    if not sid:
        _say("no session id: export GRIP_SESSION_ID=<id> (Claude Code sets "
             "CLAUDE_CODE_SESSION_ID itself)")
        raise SystemExit(2)
    return sid


def tree_relative(tree: str, cwd: str, target: str) -> Optional[str]:
    """`target` (relative to cwd) as a tree-relative posix path; None if outside.

    Only the parent is resolved, so a claimed symlink stays the symlink rather
    than becoming whatever it points at. The tree is resolved too, because git
    reports ~/.claude as its real path.
    """
    joined = os.path.join(cwd, target)
    parent = os.path.realpath(os.path.dirname(joined) or ".")
    full = os.path.join(parent, os.path.basename(joined))
    rel = os.path.relpath(full, os.path.realpath(tree)).replace(os.sep, "/")
    if rel == ".." or rel.startswith("../"):
        return None
    return "" if rel == "." else rel


def match_paths(candidates: Sequence[str], rel: str) -> list[str]:
    """Candidates equal to `rel` or under it ('' matches every candidate)."""
    if rel == "":
        return list(candidates)
    prefix = rel.rstrip("/") + "/"
    return [path for path in candidates if path == rel or path.startswith(prefix)]


def _base(args: argparse.Namespace) -> str:
    """Where CLI paths are relative to: the named --tree, else the cwd."""
    return os.path.abspath(args.tree) if args.tree else os.getcwd()


def _cmd_claim(args: argparse.Namespace) -> int:
    sid = _require_sid()
    ctx = ctx_for(args.tree)
    tree, ledger = ctx.toplevel(), ctx.ledger_dir()
    snap = snapshot(ctx, tree)
    rels: set[str] = set()
    for target in args.paths:
        rel = tree_relative(tree, _base(args), target)
        hits = match_paths(sorted(snap), rel) if rel is not None else []
        if not hits:
            _say(f"not dirty (or outside {tree}), nothing to claim: {target}")
        rels.update(hits)
    at = utc_now()
    claims = build_claims(tree, snap, sorted(rels), "claim", at)
    entry = op_entry(at, ["claim", *args.paths], ctx, tree, 0, sorted(rels), [])
    update_manifest(ledger, sid, lambda data: apply_record(
        data, tree=tree, claims=claims, released=[], op_entry=entry))
    print(f"claimed {len(rels)} path(s) for session {sid[:8]}")
    return 0


def _cmd_release(args: argparse.Namespace) -> int:
    sid = _require_sid()
    ctx = ctx_for(args.tree)
    tree, ledger = ctx.toplevel(), ctx.ledger_dir()
    released: list[str] = []

    def mutate(data: dict) -> None:
        mine = [c["path"] for c in data["claims"].values() if c.get("tree") == tree]
        for target in args.paths:
            rel = tree_relative(tree, _base(args), target)
            released.extend(match_paths(mine, rel) if rel is not None else [])
        at = utc_now()
        apply_record(data, tree=tree, claims={}, released=released,
                     op_entry=op_entry(at, ["release", *args.paths], ctx, tree, 0,
                                       [], released))

    update_manifest(ledger, sid, mutate)
    print(f"released {len(set(released))} claim(s) for session {sid[:8]}")
    return 0


def _cmd_show(args: argparse.Namespace) -> int:
    sid = _require_sid()
    ctx = ctx_for(args.tree)
    data = load_manifest(manifest_path(ctx.ledger_dir(), sid)) or new_manifest(sid)
    if args.json:
        print(json.dumps(data, indent=1, sort_keys=True))
        return 0
    print(f"session {sid}  claims {len(data['claims'])}  stashes "
          f"{len(data['stashes'])}  ops {len(data['ops'])}")
    for claim in sorted(data["claims"].values(), key=lambda c: (c["tree"], c["path"])):
        print(f"  {claim['status']:2}  {claim['tree']}/{claim['path']}  ({claim['op']}, {claim['at']})")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="grip_dirty_snapshot", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    for verb, helptext in (("claim", "claim dirty paths for this session"),
                           ("release", "drop this session's claims on paths")):
        verb_parser = sub.add_parser(verb, help=helptext)
        verb_parser.add_argument("--tree", default=None, help="repository to act on")
        verb_parser.add_argument("paths", nargs="+")
    show = sub.add_parser("show", help="print this session's manifest")
    show.add_argument("--tree", default=None, help="repository to act on")
    show.add_argument("--json", action="store_true")
    return parser


_DISPATCH = {"claim": _cmd_claim, "release": _cmd_release, "show": _cmd_show}


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        return _DISPATCH[args.cmd](args)
    except LedgerError as exc:
        _say(str(exc))
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
