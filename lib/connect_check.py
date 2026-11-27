"""The /connect worktree and check step: git, a secrets scan and the tests -- no model.

A foreign model wrote the change, so nothing here takes its word. The changed
files come from git, the scope is compared with the plan, the diff is scanned
for credentials, and the change's own tests must fail on the base commit and
pass on the head. Every argv is built here; no model text reaches a shell.

The worktree leaves out every git-crypt path: checkout runs with the decrypting
filter switched off, and each encrypted file is then marked skip-worktree and
removed, so neither plaintext nor ciphertext of an encrypted file is in front
of a coder. Nothing in the repository's shared config changes.

A run's ``hide`` globs leave out more tracked files the same way, in the coder's
worktree and in both test worktrees. A listing or a search of the worktree's
folder then finds none of them. It is not confinement: the index still names
them (``git ls-files``), and their contents stay in the shared object store,
which ``git show`` and ``git grep --cached`` read. Reads outside the worktree
stay open too (GRIP#9154).

The tests run under Codex's seatbelt profile ``:workspace``. Measured
2026-11-11 on codex-cli 0.156.1 from a directory outside /tmp: writes to the
parent directory and to $HOME were blocked, and so were curl and a raw socket.
Reads, /tmp and $TMPDIR stayed open. Those residuals are stated, not hidden,
and they are why a run's worktree must live outside /tmp.
"""
from __future__ import annotations

import fnmatch
import os
import re
import shlex
import shutil
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

TMP_DIRNAME = ".connect-tmp"
NO_DECRYPT = ("-c", "filter.git-crypt.smudge=cat", "-c", "filter.git-crypt.required=false")
#: Writing the index re-reads every racily clean entry through its clean filter, and
#: git-crypt's clean filter fails in a worktree that has no key. The encrypted files there
#: hold the very bytes the index holds, because checkout never decrypted them, so reading
#: them as they lie (cat) is the true answer, and plaintext can never reach the index.
AS_STORED = NO_DECRYPT + ("-c", "filter.git-crypt.clean=cat")
#: Every git call here runs with hooks off. The conductor runs git outside the
#: sandbox, so a hook the coder rewrote (or a script a hook calls) would run on
#: the host unsandboxed; --no-verify alone misses post-checkout and the rest.
NO_HOOKS = ("-c", "core.hooksPath=/dev/null")

#: Paths a /connect run may never change, whatever the plan says: CI, hooks,
#: gates, rules, settings, agent instructions, and the git files (at any depth)
#: that decide what git ignores, encrypts or fetches. The merge sweep's own list
#: is added when the target repository carries bin/grip-pr-monitor.
FORBIDDEN_RE = re.compile(
    r"^(\.github/|\.git-crypt/|hooks/|lib/gates/|rules/|\.claude/)"
    r"|(^|/)(CLAUDE|AGENTS)\.md$|(^|/)\.git(attributes|ignore|modules)$"
    r"|^(config/)?settings[^/]*\.json$|^config/hooks\.registry\.json$")


class CheckError(RuntimeError):
    """A git or process step the check depends on did not complete."""


def git(cwd, *args: str, stdin: str = "", check: bool = True, timeout: int = 120) -> str:
    """Run git in ``cwd`` with hooks off and return stdout; raise CheckError on a non-zero exit."""
    res = subprocess.run(["git", "-C", str(cwd), *NO_HOOKS, *args], input=stdin, capture_output=True,
                         text=True, timeout=timeout)
    if check and res.returncode != 0:
        raise CheckError(f"git {' '.join(args[:3])} failed: {res.stderr.strip()[:300]}")
    return res.stdout


def git_crypt_paths(cwd, paths, source: str = "") -> list:
    """The subset of ``paths`` that the repository's attributes send through git-crypt."""
    paths = [p for p in paths if p]
    if not paths:
        return []
    extra = ("--source", source) if source else ()
    out = git(cwd, "check-attr", "-z", *extra, "--stdin", "filter", stdin="\0".join(paths) + "\0")
    parts = out.split("\0")
    return [parts[i] for i in range(0, len(parts) - 2, 3) if parts[i + 2] == "git-crypt"]


def forbidden_matcher(repo):
    """A predicate for paths a run may not touch, with the merge sweep's list when present."""
    try:
        from lib.merge_yourself import machinery_pattern

        sweep = machinery_pattern(Path(repo))
    except Exception:  # noqa: BLE001 -- a repository with no sweep has only the fixed list
        sweep = None
    return lambda path: bool(FORBIDDEN_RE.search(path) or (sweep and sweep.search(path)))


def hidden_by(paths, patterns) -> list:
    """The subset of ``paths`` that match a ``hide`` glob. The rules are fnmatch's, case
    sensitive, and ``*`` also matches a ``/``, so ``notes/*`` covers every depth below it."""
    return [p for p in paths if p and any(fnmatch.fnmatchcase(p, pat) for pat in patterns)]


def tracked_files(cwd, ref: str) -> list:
    """Every file tracked at ``ref``. A submodule entry is left out: its checkout is a
    directory, not a file, and its contents are not in this repository's tree."""
    entries = git(cwd, "ls-tree", "-r", "-z", ref).split("\0")
    return [e.split("\t", 1)[1] for e in entries if "\t" in e and e.split(" ", 2)[1] == "blob"]


def make_worktree(repo, ref: str, dest, hide=()) -> Path:
    """A detached worktree at ``ref`` with every git-crypt file, and every tracked file
    that matches a ``hide`` glob, left out. A worktree that fails part-way is removed."""
    dest = Path(dest)
    git(repo, *NO_DECRYPT, "worktree", "add", "--detach", str(dest), ref, timeout=600)
    try:
        _leave_out(dest, hide)
    except BaseException:
        remove_worktree(repo, dest)
        raise
    return dest


def _leave_out(dest: Path, hide) -> None:
    tracked = tracked_files(dest, "HEAD")
    hidden = sorted(set(git_crypt_paths(dest, tracked)) | set(hidden_by(tracked, hide)))
    if hidden:
        git(dest, *AS_STORED, "update-index", "--skip-worktree", "-z", "--stdin", stdin="\0".join(hidden) + "\0")
        _unlink_each(dest, hidden)
    tmp = dest / TMP_DIRNAME
    tmp.mkdir(exist_ok=True)
    (tmp / ".gitignore").write_text("*\n", encoding="utf-8")


def _unlink_each(root: Path, rel_paths) -> None:
    for rel in rel_paths:
        (root / rel).unlink(missing_ok=True)


def remove_worktree(repo, dest) -> None:
    """Remove a worktree this module made. The branch, if any, stays."""
    git(repo, "worktree", "remove", "--force", str(dest), check=False)


def _porcelain_paths(out: str) -> list:
    paths, items = [], iter(out.split("\0"))
    for item in items:
        if len(item) > 3:
            paths.append(item[3:])
            if item[0] in "RC":
                paths.append(next(items, ""))
    return sorted({p for p in paths if p})


def changed_files(wt) -> list:
    """Every file the coder created, changed or deleted, read from git, never from the coder."""
    return _porcelain_paths(git(wt, "status", "--porcelain=v1", "-z", "--untracked-files=all"))


def scope_faults(files, allowed, forbidden) -> list:
    """What breaks the plan's scope: a file outside it, or a path no run may touch."""
    if not files:
        return ["the coder changed nothing"]
    allowed = set(allowed)
    outside = [f"changed outside the plan: {f}" for f in files if f not in allowed]
    banned = [f"a protected path changed: {f}" for f in files if forbidden(f)]
    return outside + banned


def staged_diff(wt, files) -> str:
    """Stage exactly ``files`` and return the diff of the index against the base."""
    if files:
        git(wt, "add", "-A", "--", *files)
    return git(wt, "diff", "--cached", "--no-color", "--no-ext-diff", "--no-renames", "HEAD")


def binary_files(wt) -> list:
    """Staged files git treats as binary. Their bytes never reach the diff, so the
    secrets scan cannot read them, and the check refuses them instead."""
    out = git(wt, "diff", "--cached", "--numstat", "-z", "--no-renames", "HEAD")
    return [entry.split("\t", 2)[2] for entry in out.split("\0") if entry.startswith("-\t-\t")]


def is_test_side(path: str) -> bool:
    """A test file or a test-support file: under a tests/ or test/ directory, a
    ``test_*`` file, or a ``conftest.py``."""
    parts = path.split("/")
    return (parts[-1] == "conftest.py" or parts[-1].startswith("test_")
            or any(part in ("tests", "test") for part in parts[:-1]))


def staged_commit(wt, base_ref: str) -> tuple:
    """(tree, commit): the index as a tree, and a commit of it on the base that no branch names."""
    tree = git(wt, "write-tree").strip()
    commit = git(wt, "commit-tree", tree, "-p", base_ref, "-m", "connect: the staged tree under test").strip()
    return tree, commit


def quotable(diff: str) -> str:
    """The added and removed lines of a diff, without their +/- marks."""
    return "\n".join(line[1:] for line in diff.splitlines()
                     if line[:1] in "+-" and not line.startswith(("+++", "---")))


#: Filter, diff and fsmonitor commands git may run while the conductor stages or
#: diffs a change. git runs them outside the sandbox and core.hooksPath does not
#: stop them, so only known binaries are allowed; a command that names a script
#: the coder could edit refuses the whole repository.
SAFE_DRIVERS = frozenset({"git-crypt", "git-lfs"})
_DRIVER_KEY = re.compile(
    r"^(filter\.[^.]+\.(clean|smudge|process)|diff\.[^.]+\.(textconv|command)|core\.fsmonitor)$")


def _driver_safe(repo, key: str, value: str) -> bool:
    if key == "core.fsmonitor":
        return value.strip().lower() in ("", "true", "false")
    try:
        first = (shlex.split(value) or [""])[0]
    except ValueError:
        return False
    if Path(first).name not in SAFE_DRIVERS:
        return False
    return "/" not in first or not Path(first).resolve().is_relative_to(Path(repo).resolve())


def unsafe_drivers(repo) -> list:
    """Configured git commands that could run coder-written code: every one not on the allowlist."""
    out = git(repo, "config", "--null", "--get-regexp", r"^(filter|diff)\.|^core\.fsmonitor$", check=False)
    pairs = [entry.split("\n", 1) for entry in out.split("\0") if "\n" in entry]
    return [f"{key}={value}" for key, value in pairs if _DRIVER_KEY.match(key) and not _driver_safe(repo, key, value)]


def symlink_faults(wt, files) -> list:
    """A changed symlink whose target lies outside the worktree: tests could pass on host-only content."""
    root = Path(wt).resolve()
    links = [f for f in files if (Path(wt) / f).is_symlink()]
    return [f"a symlink points outside the worktree: {f}" for f in links
            if not (Path(wt) / f).resolve().is_relative_to(root)]


def redacted(text: str) -> str:
    """``text`` with every credential-shaped string masked."""
    from lib.secret_redactor import redact_secrets

    return redact_secrets(text)


def secret_hits(text: str) -> int:
    """How many credential-shaped strings the redactor finds in ``text``."""
    from lib.secret_redactor import MASK, redact_secrets

    return redact_secrets(text).count(MASK) - text.count(MASK)


# --- supervised processes ------------------------------------------------------------


@dataclass
class Finished:
    """How a supervised process ended."""

    rc: int | None
    reason: str          # "exit", "timeout", or the stop reason
    seconds: float
    output: str          # the last 4,000 characters of stdout and stderr

    @property
    def passed(self) -> bool:
        return self.reason == "exit" and self.rc == 0

    @property
    def red(self) -> bool:
        """pytest's "tests failed" (1) or "errors during collection" (2); never a usage error."""
        return self.reason == "exit" and self.rc in (1, 2)

    def facts(self) -> dict:
        return {"rc": self.rc, "reason": self.reason, "ms": int(self.seconds * 1000)}


def _kill_group(proc: subprocess.Popen) -> None:
    """Stop the process group this module started -- by its id, never a name match."""
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(proc.pid, sig)
            proc.wait(timeout=5)
            return
        except ProcessLookupError:
            return
        except subprocess.TimeoutExpired:
            continue


def _wait(proc: subprocess.Popen, start: float, timeout: float, should_stop, poll: float) -> str:
    while True:
        try:
            proc.wait(timeout=poll)
            return "exit"
        except subprocess.TimeoutExpired:
            pass
        why = should_stop() or ("timeout" if time.monotonic() - start > timeout else "")
        if why:
            _kill_group(proc)
            return why


def _tail(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")[-4000:] if path.exists() else ""


def supervise(argv, *, env, cwd, out_path, stdin_path=None, timeout: float = 300.0,
              should_stop=lambda: "", poll: float = 5.0) -> Finished:
    """Run ``argv`` in its own process group, polling ``should_stop`` and the clock."""
    start = time.monotonic()
    with open(out_path, "wb") as out, open(stdin_path or os.devnull, "rb") as stdin:
        proc = subprocess.Popen(argv, cwd=str(cwd), env=env, stdin=stdin, stdout=out,
                                stderr=subprocess.STDOUT, start_new_session=True)
        reason = _wait(proc, start, timeout, should_stop, poll)
    rc = proc.returncode if reason == "exit" else None
    return Finished(rc, reason, time.monotonic() - start, _tail(Path(out_path)))


# --- the tests: red on the base, green on the head ----------------------------------------


def sandboxed_pytest_argv(wt, python: str, test_paths) -> list:
    """pytest under the Codex seatbelt profile ``:workspace``, rooted at the worktree.

    Not named ``test_*``: pytest collects a module-level ``test_*`` function
    that a test file imports.
    """
    return ["codex", "sandbox", "-P", ":workspace", "-C", str(wt), "--",
            python, "-m", "pytest", "-q", "-p", "no:cacheprovider", *test_paths]


def sandboxed_pytest_env(wt, codex_home) -> dict:
    """An allowlisted environment: the worktree's own code, its own temp dir, no keys."""
    wt = Path(wt)
    return {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": os.environ.get("HOME", ""),
            "LANG": "en_US.UTF-8", "TMPDIR": str(wt / TMP_DIRNAME), "PYTHONPATH": str(wt),
            "CLAUDE_DIR": str(wt), "PYTHONDONTWRITEBYTECODE": "1", "CODEX_HOME": str(codex_home),
            "OPENAI_API_KEY": "", "CODEX_API_KEY": ""}


def _copy_tests(src: Path, dest: Path, test_paths) -> None:
    for rel in test_paths:
        if (src / rel).is_file():
            (dest / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src / rel, dest / rel)


def _base_run(repo, base_ref: str, head_wt: Path, name: Path, copy, run, hide=()):
    base_wt = make_worktree(repo, base_ref, name, hide=hide)
    try:
        _copy_tests(head_wt, base_wt, sorted(set(copy)))
        return run(base_wt)
    finally:
        remove_worktree(repo, base_wt)


def _red_green_verdict(tree: str, head, before) -> dict:
    if not head.passed:
        ok, why, output = False, "the change's tests do not pass on the staged tree", head.output
    elif not before.red:
        ok, why, output = (False, "the change's tests do not fail on the base commit, so they prove nothing",
                           before.output)
    else:
        ok, why, output = True, "red on the base commit, green on the staged tree", ""
    return {"ok": ok, "why": why, "tree": tree, "head": head.facts(),
            "base": before.facts() if before else None, "output": output}


def red_green(repo, base_ref: str, wt, test_paths, support, run, hide=()) -> dict:
    """The change's tests must pass on the staged tree and fail on ``base_ref``.

    ``run(worktree) -> Finished``. The head run gets a clean worktree of exactly
    the staged tree, so an ignored or unstaged file in the coder's worktree can
    never make a test pass that the commit would fail. The base run gets a
    worktree at the base commit with the change's test files AND its changed
    test-support files (``support``: a conftest.py, a tests/ helper) copied in,
    so a missing helper is never mistaken for proof that the change is needed.
    Both leave out the ``hide`` globs too: a test's output goes back to the coder
    in a repair round, so a test must not be able to print a hidden file.
    """
    wt = Path(wt)
    tree, commit = staged_commit(wt, base_ref)
    head_wt = make_worktree(repo, commit, wt.with_name(wt.name + "-head"), hide=hide)
    try:
        head = run(head_wt)
        before = (_base_run(repo, base_ref, head_wt, wt.with_name(wt.name + "-base"),
                            [*test_paths, *support], run, hide=hide) if head.passed else None)
    finally:
        remove_worktree(repo, head_wt)
    return _red_green_verdict(tree, head, before)
