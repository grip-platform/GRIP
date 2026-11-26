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

Before the real export, a second, throwaway ``git archive`` invocation
(``archive_file_count``) lists the same archive to count how many regular files
it will actually hold -- the floor the real export is checked against, so every
export now costs one extra ``git archive`` on top of the one that writes it
(GRIP#9643).
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
# read without a checkout, so the attribute-aware check below (process-filter
# usage) falls back to its pre-2026-11 behaviour (GRIP#9551). tree_file_count no
# longer reads an attribute at all, so it no longer needs this gate (GRIP#9643).
_MIN_CHECK_ATTR_SOURCE_GIT = (2, 40)

# Shared by archive_file_count's counting pass and export_tree's real extraction,
# so the two invocations of the same archive can never silently diverge under a
# future edit that changes one and not the other (xAI Grok 4.3, AGORA review on
# PR for GRIP#9643).
_ARCHIVE_TIMEOUT_S = 600


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

    Also raises when the answer's field count does not match ``3 * len(paths) +
    1`` -- the shape ``-z --stdin`` always gives for a well-formed answer -- so a
    truncated or malformed response is never misread as fewer paths than were
    asked, which could silently shift a later path onto the wrong attribute
    value (GRIP#9643).
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
    if len(fields) != 3 * len(paths) + 1:
        raise RuntimeError(
            f"git check-attr {attribute!r} on {sha[:9]} returned {len(fields)} fields "
            f"for {len(paths)} paths"
        )
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
    """How many regular files ``sha``'s tree lists. Symlinks and submodules are
    never counted.

    This is the RAW count: a file whose own ``.gitattributes`` sets
    ``export-ignore`` is still in the tree and still counted here. What ``git
    archive`` itself will actually omit -- a file, or an entire folder marked
    that way, which a per-file ``check-attr`` call can never see, since the
    attribute is set on the folder's own path and never on each file beneath it
    (GRIP#9643) -- is ``archive_file_count``'s job: it asks ``git archive``
    directly instead of re-deriving its exclusion rule in Python.
    """
    listing = _git(git_dir, "ls-tree", "-r", "-z", sha)
    if listing.returncode != 0:
        raise RuntimeError(f"git ls-tree {sha[:9]} failed: {listing.stderr.strip()[:200]}")
    return sum(
        1 for entry in listing.stdout.split("\0")
        if entry and entry.split(" ", 1)[0] in _REGULAR_FILE_MODES
    )


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


def _archive_listing(git_dir: Path, sha: str) -> str:
    """Run ``git archive`` for ``sha`` and return its ``tar -tvf -`` text.

    Raises on a timeout or any other failure; the archive process is always
    reaped first, win or lose -- not only a timeout can strand it, so a bare
    ``except BaseException`` covers a missing ``tar`` binary and anything
    else too (GRIP#9548, hardened for GRIP#9643: Alibaba Qwen4-Max's AGORA
    review on that PR found only the timeout path reaped it here).
    """
    archive = subprocess.Popen(
        ["git", "-C", str(git_dir), *_stored_blob_options(git_dir, sha), "archive", sha],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
    )
    try:
        # encoding/errors pinned the same way `_git` pins them: a filename that
        # is not valid UTF-8 must never turn into an unhandled
        # UnicodeDecodeError that silently demotes a genuine export to the
        # working-folder fallback (same review).
        listing = subprocess.run(["tar", "-tvf", "-"], stdin=archive.stdout,
                                 capture_output=True, text=True, encoding="utf-8",
                                 errors="replace", timeout=_ARCHIVE_TIMEOUT_S)
    except subprocess.TimeoutExpired as exc:
        archive_rc = _end_archive(archive)
        raise RuntimeError(
            f"could not count {sha[:9]}'s archive: tar timed out after {exc.timeout}s "
            f"(git archive exit={archive_rc})"
        ) from exc
    except BaseException:
        _end_archive(archive)
        raise
    archive_rc = _end_archive(archive)
    if archive_rc != 0 or listing.returncode != 0:
        raise RuntimeError(
            f"could not count {sha[:9]}'s archive: git archive exit={archive_rc}, "
            f"tar exit={listing.returncode}: {listing.stderr[:200]}"
        )
    return listing.stdout


def archive_file_count(git_dir: Path, sha: str) -> int:
    """How many regular files ``git archive`` itself writes for ``sha`` -- the
    real floor ``export_tree`` checks its own output against.

    ``git archive`` decides what ``export-ignore`` excludes, in every form the
    attribute takes -- one file, a whole folder, a machine-wide attributes
    file -- and on every git version. Asking it directly, by listing the SAME
    archive ``export_tree`` would extract, never misses a case the way
    re-deriving the rule in Python can: a per-file ``check-attr`` call reports
    a file inside an export-ignore'd FOLDER as unspecified, because the
    attribute is set on the folder's own path, never on each file beneath it
    (GRIP#9643).

    Known limitation, accepted by the decision brief this fix implements:
    this floor and the real extraction both ultimately run ``git archive`` on
    the same ``sha``, so a deterministic defect IN git archive itself (unlike
    a transient I/O failure, which GRIP#9548's pipe handling still catches)
    could pass both and go unnoticed -- the old ``ls-tree``-based floor was a
    fully independent oracle, this one is not (Alibaba Qwen4-Max, AGORA
    review on PR for GRIP#9643). ``_skipped_files``'s own comparison against
    the raw tree count is the nearest available check on that.
    """
    listing = _archive_listing(git_dir, sha)
    # tar -tv's permission column's first character is the entry type: '-' for a
    # regular file, 'd' for a directory, 'l' for a symlink -- the same regular-
    # file/symlink distinction `_count_files` makes after a real extraction. Split
    # on tar's own line terminator explicitly, never str.splitlines()'s wider set
    # (vertical tab, Unicode line/paragraph separators, ...): those are legal
    # inside a FILENAME, which sits at the end of each line, and splitting on
    # one there would miscount (same review). A tar implementation that instead
    # escapes a literal newline WITHIN a filename as text (verified on this
    # machine's bsdtar) sidesteps the one case splitting on "\n" alone cannot.
    return sum(1 for line in listing.split("\n") if line.startswith("-"))


def export_tree(git_dir: Path, sha: str, dest: Path, min_files: Optional[int] = None) -> int:
    """Write the tree of ``sha`` into ``dest`` and return its file count.

    A pipe's exit status is no evidence that files arrived (rules/sync-mirror-safety.md),
    so the export is judged by the regular files it produced: at least ``min_files``,
    which by default is however many regular files ``git archive`` itself writes
    for ``sha`` (``archive_file_count``, GRIP#9643) -- never a count predicted
    from ``.gitattributes``, which can diverge from what ``git archive``
    actually excludes.

    Whichever way ``tar`` ends -- a clean exit, a failure, or a timeout -- the
    ``git archive`` process on the other end of the pipe is always closed down
    via ``_end_archive``, and never left running (GRIP#9548).
    """
    _fetch_commit(git_dir, sha)
    floor = archive_file_count(git_dir, sha) if min_files is None else min_files
    archive = subprocess.Popen(["git", "-C", str(git_dir), *_stored_blob_options(git_dir, sha), "archive", sha],
                               stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    try:
        untar = subprocess.run(["tar", "-x", "-C", str(dest)], stdin=archive.stdout,
                               capture_output=True, timeout=_ARCHIVE_TIMEOUT_S)
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


def _skipped_files(clone: Path, sha: str, files: int, pr: int) -> Optional[int]:
    """How many of the commit's regular files a genuine export left out, or
    None when that count could not be established safely -- never 0 for a
    failure, which would read as "confirmed nothing was skipped" and conflate
    exactly the two meanings this PR's own check-attr hardening separated for
    a different caller, two functions earlier (Alibaba Qwen4-Max, AGORA
    review on PR for GRIP#9643).

    The tree can only ever list AT LEAST as many regular files as a correct
    export writes -- git archive never invents a file. A smaller tree count
    than the export's own file count is therefore a counting bug somewhere,
    not "nothing was skipped", and is reported the same way: as None, logged
    loudly rather than silently.
    """
    try:
        tree_total = tree_file_count(clone, sha)
    except Exception as exc:  # noqa: BLE001 -- a good export beats losing it over this count
        logger.warning(
            "AGORA: could not count PR #%s's full tree to compare against its export "
            "(%s: %s); the skipped-files notice is omitted.", pr, type(exc).__name__, exc)
        return None
    if tree_total < files:
        logger.warning(
            "AGORA: PR #%s's tree count (%d) is less than its export's own file count "
            "(%d); the skipped-files notice is omitted.", pr, tree_total, files)
        return None
    return tree_total - files


def _export_for_review(pr: int, repo: str) -> tuple[Optional[Path], Optional[str], int]:
    """The PR's commit exported for one review, or ``(None, reason, 0)`` to read
    the current folder instead. The log keeps the full detail, including any
    local path; ``reason`` is the shorter, path-free, single-line form the
    posted review names (GRIP#9551, hardened for GRIP#9596 concern 3).

    The third value is how many of the commit's regular files the export
    itself left out (``export-ignore``, GRIP#9643): 0 when nothing was left
    out, or when there was no genuine export to measure.
    """
    clone = local_clone(repo)
    if clone is None:
        reason = f"no local clone is known for {repo}"
        logger.warning(
            "AGORA: %s, so the review reads the current folder (%s), which may not hold "
            "PR #%s's own files. Pass --repo-root to name a folder that does.",
            reason, Path.cwd(), pr)
        return None, _public_reason(reason), 0
    try:
        dest, sha, files = export_pr(pr, repo, clone)
    except Exception as exc:  # noqa: BLE001 -- a review with weaker context still beats none
        logger.warning(
            "AGORA: could not export PR #%s of %s from %s (%s: %s), so the review reads the "
            "current folder (%s) instead; its file context and quote checks may not match the PR.",
            pr, repo, clone, type(exc).__name__, exc, Path.cwd())
        reason = f"could not export PR #{pr} of {repo} ({type(exc).__name__}: {exc})"
        return None, _public_reason(reason), 0
    logger.info("AGORA: reading PR #%s's own files: commit %s exported from %s into %s (%d files).",
                pr, sha[:9], clone, dest, files)
    skipped = _skipped_files(clone, sha, files, pr) or 0
    if skipped:
        logger.info(
            "AGORA: PR #%s's export left out %d file(s) that %s's tree lists (export-ignore).",
            pr, skipped, sha[:9])
    return dest, None, skipped


@contextlib.contextmanager
def review_root(
    pr: int, repo: str, repo_root: Optional[str] = None,
) -> Iterator[tuple[Optional[str], Optional[str], int]]:
    """The folder AGORA reads PR ``pr``'s files from, for the length of one review,
    why it fell back to the working folder when it did, and how many of the
    commit's files a genuine export still left out.

    Yields ``(root, fallback_reason, files_skipped)``. ``fallback_reason`` is
    None when the review reads an export of the PR's own commit, or an explicit
    ``repo_root`` — reading the working folder inside GitHub Actions is correct
    by construction there, not a fallback, so that case is also None.
    Otherwise it is the plain-English reason the export did not happen, for a
    caller to put in front of a reader (GRIP#9551): no known clone, or a
    failed or too-small export. ``files_skipped`` is how many of the commit's
    regular files ``git archive`` itself excluded (``export-ignore``,
    GRIP#9643) from a genuine export; 0 when nothing was excluded, or when
    there was no genuine export to measure.
    """
    if repo_root:
        yield repo_root, None, 0
        return
    if os.environ.get("GITHUB_ACTIONS") == "true":
        yield None, None, 0
        return
    dest, reason, skipped = _export_for_review(pr, repo)
    try:
        yield (str(dest) if dest else None), reason, skipped
    finally:
        if dest:
            shutil.rmtree(dest, ignore_errors=True)
