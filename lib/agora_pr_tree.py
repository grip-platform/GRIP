"""One pull request's own files, exported read-only, for AGORA and its seat trials.

AGORA shows its speakers the full text of each changed file, and its binding
context, symbol check and quote gate read files too. In GitHub Actions they read
the working folder, which is a checkout of the pull request. In a session the
working folder is wherever the command runs, usually the shared tree: it lacks the
PR's own changes, can lag main, and for a HAL PR is the wrong repository
(GRIP#9481). The seat trials met the same defect (Codex, GRIP#9438).

So outside Actions AGORA exports the PR's commit (its merge commit once merged,
else its head) from a local clone of the PR's own repository into a temporary
folder, reads every file from there, and removes the folder afterwards
(``review_root``). The trials run inside the same export (``pr_tree``).

The export writes every blob as stored. ``git archive`` runs smudge filters as a
checkout does, so in a clone unlocked with git-crypt it would write decrypted
files, while CI's checkout has no key and reads them as ciphertext. Each
configured smudge command is replaced with ``cat`` for the one archive call; a
long-running ``process`` filter cannot be replaced that way, so it stops the
export -- but only when some file in the commit actually carries that filter.
One merely CONFIGURED but unused, such as git-lfs's global ``filter.lfs.process``
on a machine with no LFS-attributed file, does not (GRIP#9551). The export never
touches the clone's checkout or branches: ``git fetch`` of one commit and
``git archive`` change neither.
"""
from __future__ import annotations

import contextlib
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Iterator, Optional

logger = logging.getLogger(__name__)

# The GRIP checkout this file belongs to, which is the one AGORA runs from.
_AGORA_CHECKOUT = Path(__file__).resolve().parent.parent

# owner/name (lowercase) -> the folder of its local clone. GRIP: the checkout AGORA
# runs from. HAL: HAL_HOME, else ~/.hal. Any other repository has no known clone.
_CLONES = {
    "codetonight-sa/grip": lambda: os.environ.get("CLAUDE_DIR") or str(_AGORA_CHECKOUT),
    "codetonight-sa/hal": lambda: os.environ.get("HAL_HOME") or str(Path.home() / ".hal"),
}

_REGULAR_FILE_MODES = frozenset({"100644", "100755"})

# `git check-attr --source=<tree-ish>` (attributes read from a commit, never the
# working tree) shipped in git 2.40. Below that, a repo-level attribute cannot be
# read without a checkout, so both attribute-aware checks below fall back to their
# pre-2026-11 behaviour (GRIP#9551).
_MIN_CHECK_ATTR_SOURCE_GIT = (2, 40)


def _git(git_dir: Path, *argv: str, timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(git_dir), *argv], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout, check=False)


def _git_version(git_dir: Path) -> tuple[int, int]:
    """``(major, minor)`` from ``git --version``, or ``(0, 0)`` if unparsable."""
    found = re.search(r"(\d+)\.(\d+)", _git(git_dir, "--version").stdout)
    return (int(found.group(1)), int(found.group(2))) if found else (0, 0)


def _can_read_commit_attrs(git_dir: Path) -> bool:
    return _git_version(git_dir) >= _MIN_CHECK_ATTR_SOURCE_GIT


def _all_paths(git_dir: Path, sha: str) -> list[str]:
    """Every path ``sha``'s tree lists, regular files, symlinks and submodules alike.

    Raises on a failed ``ls-tree`` rather than returning an empty list: silently
    reading "no paths" as "no path uses this filter" would let a live process
    filter run during the export when we simply could not ask (AGORA review on
    GRIP#9596, concern 1) -- the same shape ``tree_file_count`` already refused
    to risk for its own ``ls-tree`` call.
    """
    listing = _git(git_dir, "ls-tree", "-r", "--name-only", "-z", sha)
    if listing.returncode != 0:
        raise RuntimeError(f"git ls-tree {sha[:9]} failed: {listing.stderr.strip()[:200]}")
    return [p for p in listing.stdout.split("\0") if p]


def _check_attr(git_dir: Path, sha: str, paths: list[str], attribute: str) -> dict[str, str]:
    """``{path: value}`` for ``attribute`` as ``sha``'s OWN ``.gitattributes`` sets it.

    Reads from the commit (``--source``), never the working tree, so a checkout
    with different or no ``.gitattributes`` — or none checked out at all, which is
    the export's own case — cannot mislead this. Caller must have already checked
    ``_can_read_commit_attrs``.

    Raises on a failed ``check-attr`` rather than returning ``{}``: a caller
    asking "does any path use this filter" reads an empty dict as "no", which
    would let a live process filter run during the export on a machine where the
    check merely failed to answer (AGORA review on GRIP#9596, concern 1 -- the
    one genuine fail-open spot Speaker B found).
    """
    if not paths:
        return {}
    done = subprocess.run(
        ["git", "-C", str(git_dir), "check-attr", f"--source={sha}", "-z", "--stdin", attribute],
        input="\0".join(paths) + "\0", capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=120, check=False,
    )
    if done.returncode != 0:
        raise RuntimeError(f"git check-attr {attribute!r} on {sha[:9]} failed: {done.stderr.strip()[:200]}")
    fields = done.stdout.split("\0")
    return {fields[i]: fields[i + 2] for i in range(0, len(fields) - 2, 3)}


def pr_commit(pr: int, repo: str) -> str:
    """The commit a review reads: the PR's merge commit, or its head while open."""
    done = subprocess.run(["gh", "pr", "view", str(pr), "-R", repo, "--json", "mergeCommit,headRefOid"],
                          capture_output=True, text=True, timeout=60, check=True)
    data = json.loads(done.stdout)
    return (data.get("mergeCommit") or {}).get("oid") or data["headRefOid"]


def local_clone(repo: str) -> Optional[Path]:
    """The local clone AGORA exports ``repo``'s pull requests from, or None if none is known."""
    pick = _CLONES.get(repo.strip().lower())
    return Path(pick()).expanduser() if pick else None


def _fetch_commit(git_dir: Path, sha: str) -> None:
    """Make ``sha`` present in ``git_dir``. A fetch changes no checkout and no branch."""
    if _git(git_dir, "cat-file", "-e", f"{sha}^{{commit}}").returncode != 0:
        _git(git_dir, "fetch", "-q", "origin", sha, timeout=300)


def tree_file_count(git_dir: Path, sha: str) -> int:
    """How many regular files the commit lists, as ``git archive`` would write them.

    Symlinks and submodules are never counted. Neither is a regular file whose OWN
    ``.gitattributes`` sets ``export-ignore``: ``git archive`` leaves those out too,
    so counting them would make the export's own file-count floor unreachable
    (GRIP#9551). Skipped on a git too old for ``--source`` (see
    ``_can_read_commit_attrs``): such files are then counted, as before.
    """
    listing = _git(git_dir, "ls-tree", "-r", "-z", sha)
    if listing.returncode != 0:
        raise RuntimeError(f"git ls-tree {sha[:9]} failed: {listing.stderr.strip()[:200]}")
    paths = [
        entry.split("\t", 1)[1] for entry in listing.stdout.split("\0")
        if entry and entry.split(" ", 1)[0] in _REGULAR_FILE_MODES
    ]
    if _can_read_commit_attrs(git_dir):
        ignored = _check_attr(git_dir, sha, paths, "export-ignore")
        paths = [p for p in paths if ignored.get(p) != "set"]
    return len(paths)


def _stored_blob_options(git_dir: Path, sha: str) -> list[str]:
    """``-c`` options under which ``git archive`` writes every blob as stored.

    A configured ``process`` filter refuses the export only when some path in
    ``sha`` actually carries that filter in its OWN ``.gitattributes`` — read from
    the commit, never the working tree. ``git lfs install`` writes
    ``filter.lfs.process`` into the GLOBAL git config on every machine with
    git-lfs, so without this check every export failed, even for a commit with no
    LFS-attributed file (GRIP#9551). On a git too old for ``--source``, every
    configured process filter still refuses, as before — attributes cannot be
    read from a commit without one.
    """
    found = _git(git_dir, "config", "--get-regexp", r"^filter\..+\.(smudge|process)$")
    process_keys = []
    options: list[str] = []
    for line in found.stdout.splitlines():
        key = line.split(" ", 1)[0]
        if key.endswith(".process"):
            process_keys.append(key)
        else:
            options += ["-c", f"{key}=cat"]
    if not process_keys:
        return options
    if not _can_read_commit_attrs(git_dir):
        raise RuntimeError(f"{process_keys[0]} is configured in {git_dir}; the export cannot write its files as stored")
    used = set(_check_attr(git_dir, sha, _all_paths(git_dir, sha), "filter").values())
    for key in process_keys:
        # key.split(".", 2)[1] would read "filter.my.filter.process" as name
        # "my", losing everything after the first dot -- `[filter "my.filter"]`
        # is a real, quotable git config subsection (AGORA review on GRIP#9596,
        # concern 2), so the name is everything the "^filter\..+\.process$"
        # regex itself matched as the greedy middle, not the first dot-split.
        name = key[len("filter."):-len(".process")]
        if name in used:
            raise RuntimeError(f"{key} is configured in {git_dir}; the export cannot write its files as stored")
    return options


def _count_files(dest: Path) -> int:
    return sum(1 for path in dest.rglob("*") if path.is_file() and not path.is_symlink())


def _end_archive(archive: subprocess.Popen, timeout: int = 10) -> int:
    """Close the parent's copy of the pipe, and make sure ``archive`` has ended.

    Closing the read end IN THE PARENT first lets a ``git archive`` still writing
    hit EPIPE/SIGPIPE instead of blocking on a full pipe forever: the kernel pipe
    buffer is a fixed ~64 KB, so once ``tar`` stops draining it -- because it
    exited, or because it is still running but we gave up waiting on it -- a
    ``git archive`` with more than that left to write would otherwise block on
    its own write() call until something closes the pipe or kills it
    (GRIP#9548). Falls back to killing and reaping it if it does not end within
    ``timeout`` seconds of the pipe closing.
    """
    if archive.stdout is not None:
        archive.stdout.close()
    try:
        return archive.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        archive.kill()
        return archive.wait()


def export_tree(git_dir: Path, sha: str, dest: Path, min_files: Optional[int] = None) -> int:
    """Write the tree of ``sha`` into ``dest`` and return its file count.

    A pipe's exit status is no evidence that files arrived (rules/sync-mirror-safety.md),
    so the export is judged by the regular files it produced: at least ``min_files``,
    which by default is every regular file the commit lists.

    Whichever way ``tar`` ends -- a clean exit, a failure, or a timeout -- the
    ``git archive`` process on the other end of the pipe is always closed down
    via ``_end_archive``, and never left running (GRIP#9548).
    """
    _fetch_commit(git_dir, sha)
    floor = tree_file_count(git_dir, sha) if min_files is None else min_files
    archive = subprocess.Popen(["git", "-C", str(git_dir), *_stored_blob_options(git_dir, sha), "archive", sha],
                               stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    try:
        untar = subprocess.run(["tar", "-x", "-C", str(dest)], stdin=archive.stdout,
                               capture_output=True, timeout=600)
    except subprocess.TimeoutExpired as exc:
        archive_rc = _end_archive(archive)
        raise RuntimeError(
            f"could not export {sha[:9]}: tar timed out after {exc.timeout}s "
            f"(git archive exit={archive_rc})"
        ) from exc
    archive_rc = _end_archive(archive)
    if archive_rc != 0 or untar.returncode != 0:
        raise RuntimeError(
            f"could not export {sha[:9]}: git archive exit={archive_rc}, tar exit={untar.returncode}: "
            f"{untar.stderr.decode(errors='replace')[:200]}"
        )
    count = _count_files(dest)
    if count < floor:
        raise RuntimeError(f"the export of {sha[:9]} holds {count} files; expected at least {floor}")
    return count


def export_pr(pr: int, repo: str, git_dir: Path) -> tuple[Path, str, int]:
    """Export PR ``pr``'s commit from ``git_dir`` into a new temporary folder.

    Returns ``(folder, commit, files)`` and the caller removes the folder. A failed
    export removes its own folder and raises.
    """
    sha = pr_commit(pr, repo)
    # resolve(): macOS links /var to /private/var, so the unresolved path and
    # Path.cwd() would name one folder two ways.
    dest = Path(tempfile.mkdtemp(prefix=f"agora-pr-{pr}-")).resolve()
    try:
        return dest, sha, export_tree(git_dir, sha, dest)
    except BaseException:
        shutil.rmtree(dest, ignore_errors=True)
        raise


@contextlib.contextmanager
def pr_tree(pr: int, repo: str, git_dir: Path) -> Iterator[tuple[Path, str]]:
    """For the seat trials: run inside the PR's own files, yielding ``(folder, commit)``."""
    dest, sha, _files = export_pr(pr, repo, git_dir)
    here = Path.cwd()
    try:
        os.chdir(dest)
        yield dest, sha
    finally:
        os.chdir(here)
        shutil.rmtree(dest, ignore_errors=True)


def _public_reason(detail: str, limit: int = 200) -> str:
    """Collapse whitespace/newlines and cap length before ``detail`` goes into the
    posted review's fallback notice (never the log, which keeps the full detail).

    A bare exception's ``str()`` is not under our control and can run to several
    lines; rendered as-is inside the notice's one-line markdown blockquote, an
    embedded newline ends the quote early and lets whatever follows render as
    plain markdown in a PUBLIC PR comment (AGORA review on GRIP#9596, concern 3).
    """
    collapsed = _ABSOLUTE_PATH.sub("<path>", " ".join(detail.split()))
    return collapsed if len(collapsed) <= limit else f"{collapsed[:limit].rstrip()}…"


# An absolute POSIX or Windows path inside an error's text, replaced before the reason
# is capped, so neither a whole path nor half of one (a home folder names the local
# account) reaches a posted review. The look-behind keeps a slug such as owner/repo.
_ABSOLUTE_PATH = re.compile(r"(?<![\w.~-])(?:/|[A-Za-z]:\\)[^\s'\";,)]+")


def _export_for_review(pr: int, repo: str) -> tuple[Optional[Path], Optional[str]]:
    """The PR's commit exported for one review, or ``(None, reason)`` to read the
    current folder instead. The log keeps the full detail, including any local
    path; ``reason`` is the shorter, path-free, single-line form the posted
    review names (GRIP#9551, hardened for GRIP#9596 concern 3)."""
    clone = local_clone(repo)
    if clone is None:
        reason = f"no local clone is known for {repo}"
        logger.warning(
            "AGORA: %s, so the review reads the current folder (%s), which may not hold "
            "PR #%s's own files. Pass --repo-root to name a folder that does.",
            reason, Path.cwd(), pr)
        return None, _public_reason(reason)
    try:
        dest, sha, files = export_pr(pr, repo, clone)
    except Exception as exc:  # noqa: BLE001 -- a review with weaker context still beats none
        logger.warning(
            "AGORA: could not export PR #%s of %s from %s (%s: %s), so the review reads the "
            "current folder (%s) instead; its file context and quote checks may not match the PR.",
            pr, repo, clone, type(exc).__name__, exc, Path.cwd())
        reason = f"could not export PR #{pr} of {repo} ({type(exc).__name__}: {exc})"
        return None, _public_reason(reason)
    logger.info("AGORA: reading PR #%s's own files: commit %s exported from %s into %s (%d files).",
                pr, sha[:9], clone, dest, files)
    return dest, None


@contextlib.contextmanager
def review_root(
    pr: int, repo: str, repo_root: Optional[str] = None,
) -> Iterator[tuple[Optional[str], Optional[str]]]:
    """The folder AGORA reads PR ``pr``'s files from, for the length of one review,
    and why it fell back to the working folder when it did.

    Yields ``(root, fallback_reason)``. ``fallback_reason`` is None when the review
    reads an export of the PR's own commit, or an explicit ``repo_root`` — reading
    the working folder inside GitHub Actions is correct by construction there, not a
    fallback, so that case is also None. Otherwise it is the plain-English reason
    the export did not happen, for a caller to put in front of a reader
    (GRIP#9551): no known clone, or a failed or too-small export.
    """
    if repo_root:
        yield repo_root, None
        return
    if os.environ.get("GITHUB_ACTIONS") == "true":
        yield None, None
        return
    dest, reason = _export_for_review(pr, repo)
    try:
        yield (str(dest) if dest else None), reason
    finally:
        if dest:
            shutil.rmtree(dest, ignore_errors=True)
