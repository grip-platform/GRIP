#!/usr/bin/env python3
"""Let a GRIP tree fast-forward over files changed both locally and on main.

``bin/grip-tree-sync`` keeps the shared tree level with ``origin/main``. Before this
module it refused whenever main changed a file the tree had also changed: git will
not fast-forward over such a file, because before writing a path it compares the
working copy with the INDEX, never with the bytes it is about to write. The job had
refused 363 times by 2026-10-11, 311 of them over settings.json, and GRIP#7288 is
the same refusal on runtime ledgers marked skip-worktree, which no diff shows.

The route rewrites nothing a session has edited. It was first done by hand on
2026-10-11 (71a9fa750 -> 9e6050060), and this module is that run made repeatable:

1. Decide what each such working copy should become: left alone when it already
   holds main's version, otherwise a three-way merge of the copy, HEAD and main --
   JSON objects by key, JSONL ledgers by row, other text by line.
2. If any file needs a choice between two edits, change nothing and say which file
   and why; the caller declines, as before.
3. Stage main's blob for each file with ``git update-index --cacheinfo``. A
   fast-forward keeps an index entry that already matches its target and never
   reads that working file, so ``git merge --ff-only`` now goes through.
4. Write each merged copy, but only over the bytes the merge was computed from; a
   file that changed meanwhile is merged again, or left as it is.
5. Put back every skip-worktree bit that staging cleared.

Fast-forwards only: a true merge refuses an index that differs from HEAD.

Scope settled by V>> through /hitl on 2026-10-12: merge the clean cases on the
timer, decline real overlaps (bin/grip-tree-sync then tells every session), and
wait while a session holds the shared-tree lock. Later that night he added one
exception: a file in REGENERATED, which the shared tree rebuilds from its own state,
takes main's copy when the two clash, instead of declining the whole sync.

Usage (bin/grip-tree-sync pipes the candidate paths in, one per line):
    tree_sync_resolve.py --tree <dir> [--target origin/main] < paths
Exit 0: merged; one line per file, "staged|merged|replaced|left<TAB>path[<TAB>why]".
Exit 3: blocked, nothing changed; "blocked<TAB>path<TAB>why" per file.
Exit 1: failed, the index put back as it was; "error<TAB>why".
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

# lib/settings_merge.py, loaded by its OWN path rather than a bare `import
# settings_merge` (plan-v4 D1): a bare import resolves through sys.path, and
# the test suite's conftest puts the SHARED tree's lib/ there, so a bare
# import would test that copy instead of the one sitting beside this file --
# wrong whenever this module itself is loaded from a worktree under test.
_SETTINGS_MERGE = None


def _settings_merge_module():
    global _SETTINGS_MERGE
    if _SETTINGS_MERGE is None:
        spec = importlib.util.spec_from_file_location(
            "_tree_sync_settings_merge", Path(__file__).resolve().parent / "settings_merge.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _SETTINGS_MERGE = module
    return _SETTINGS_MERGE


def _is_shared_tree(tree: str) -> bool:
    """True when `tree` IS the shared CLAUDE_DIR tree. The settings.json
    ownership policy (plan-v4 C1) applies only there -- never to a feature
    worktree's own settings.json, where a genuine double edit still declines
    like any other file, so a PR's own edits are never undone and its new
    binding is never called "retired"."""
    claude_dir = os.environ.get("CLAUDE_DIR") or str(Path.home() / ".claude")
    try:
        return Path(tree).resolve() == Path(claude_dir).resolve()
    except OSError:
        return False

EXIT_MERGED = 0
EXIT_FAILED = 1
EXIT_BLOCKED = 3

# A key missing from one side of a three-way JSON merge (added or deleted there).
_ABSENT = object()

# One conflict hunk as `git merge-file -p --diff3 -L ours -L base -L theirs`
# prints it. Groups: ours, base, theirs. Every marker is a whole line.
_CONFLICT = re.compile(
    rb"^<<<<<<< ours\n(.*?)^\|\|\|\|\|\|\| base\n(.*?)^=======\n(.*?)^>>>>>>> theirs\n",
    re.S | re.M,
)
_LEFTOVER_MARKER = re.compile(rb"^(<<<<<<<|\|\|\|\|\|\|\||=======|>>>>>>>)( |$)", re.M)
# The indentation a JSON file was written with, read from its first nested line.
_INDENT = re.compile(r"\n([ \t]+)\S")

# Files the shared tree rebuilds from its own state, which main also commits as
# snapshots. When both changed, main's copy is taken instead of declining: the local
# copy is rebuilt soon after, so nothing is lost (V>>, /hitl, 2026-10-12). Each one
# blocked the sync that night. An allowlist on purpose: a file joins only by name.
#   rules/HOLISTIC.md                   lib/holistic_synthesiser.py, on every session stop
#   rules/learned-insights.md           /learn, through lib/learning_detector.py
#   apps/grip-web/public/snapshot.json  bin/grip-web-snapshot
#   skills/INDEX.md                     scripts/generate-skills-index.py, from the skills
#                                       installed on THIS machine (plugins included), so
#                                       its "Total skills" line clashes with every main
#                                       commit that adds a skill (GRIP#8715, 2026-10-27)
REGENERATED = frozenset({
    "rules/HOLISTIC.md",
    "rules/learned-insights.md",
    "apps/grip-web/public/snapshot.json",
    "skills/INDEX.md",
})


class MergeConflict(Exception):
    """The two sides cannot be combined without choosing; the message says where."""


@dataclass
class Plan:
    """What happens to one file. kind is stage, merge, replace, none or block."""

    path: str
    kind: str = "block"
    why: str = ""
    base: bytes | None = None
    ours: bytes | None = None
    theirs: bytes | None = None
    merged: bytes | None = None
    target_entry: str = ""  # "<mode>,<oid>" of main's blob, for update-index --cacheinfo
    head_entry: str = ""    # the same for HEAD, put back if the fast-forward fails
    skip_worktree: bool = False


# ---------------------------------------------------------------- git access

def _git(tree: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", tree, "-c", "core.quotePath=false", *args],
        capture_output=True, timeout=120,
    )


def _git_ok(tree: str, *args: str) -> bytes:
    done = _git(tree, *args)
    if done.returncode != 0:
        # The whole message on one line: git's last line alone is often just "Aborting".
        said = " ".join(done.stderr.decode("utf-8", "replace").split())[:300]
        raise RuntimeError(f"git {args[0]}: {said or f'exit {done.returncode}'}")
    return done.stdout


def _entry(tree: str, rev: str, path: str) -> str:
    """'<mode>,<oid>' of path at rev, or '' when rev does not have it."""
    listed = _git_ok(tree, "ls-tree", rev, "--", path).decode("utf-8", "replace").strip()
    if not listed:
        return ""
    mode, kind, oid = listed.split("\t", 1)[0].split()
    if kind != "blob":
        raise MergeConflict(f"not a regular file at {rev}")
    return f"{mode},{oid}"


def _content(tree: str, rev: str, path: str) -> bytes:
    """The file as a checkout would write it, with smudge filters (git-crypt too)."""
    return _git_ok(tree, "cat-file", "--filters", f"{rev}:{path}")


def _flagged_skip_worktree(tree: str, path: str) -> bool:
    return _git_ok(tree, "ls-files", "-v", "--", path)[:1] in (b"S", b"s")


def _working(tree: str, path: str) -> bytes | None:
    file = Path(tree, path)
    if file.is_symlink() or (file.exists() and not file.is_file()):
        raise MergeConflict("not a regular file here")
    return file.read_bytes() if file.exists() else None


# ---------------------------------------------------------------- the decision

def decide(path: str, base: bytes | None, ours: bytes | None, theirs: bytes | None,
           *, use_settings_policy: bool = False, tree: str | None = None,
           target: str | None = None) -> tuple[str, bytes | None]:
    """What the working copy becomes: (stage | merge | replace | none, bytes or None).

    `target` is the revision the tree is about to fast-forward to; the settings
    policy uses it to tell a hook script that merge brings from one that is
    genuinely missing."""
    if base is None:
        raise MergeConflict("main adds it, but a local file is already in the way")
    if theirs is None:
        if ours is None or ours == base:
            return "none", None
        raise MergeConflict("main deletes it, but it has local changes")
    if ours is None:
        raise MergeConflict("deleted here, but main changed it")
    if use_settings_policy:
        # Ahead of the raw-byte shortcuts below, on purpose: the /model picker
        # rewrites the whole live settings.json in its own key order, so
        # `ours == base` at the BYTE level is false even when nothing a human
        # would call "a change" happened -- only the semantic composer can
        # tell the two apart (plan-v4 C2).
        return _decide_settings_policy(path, base, ours, theirs, tree, target)
    if ours == theirs:
        return "stage", None
    if ours == base:
        return "merge", theirs
    return _combine(path, base, ours, theirs)


def _decide_settings_policy(path: str, base: bytes, ours: bytes, theirs: bytes,
                             tree: str | None, target: str | None = None,
                             ) -> tuple[str, bytes | None]:
    """settings.json's own three-way rule (plan-v4 C1-C4), for the shared
    tree's root file only -- see `decide`'s `use_settings_policy` guard."""
    sm = _settings_merge_module()
    try:
        base_d, ours_d, theirs_d = (json.loads(side.decode("utf-8")) for side in (base, ours, theirs))
    except ValueError as exc:  # UnicodeDecodeError is a ValueError too
        raise MergeConflict(f"one side is not valid JSON ({type(exc).__name__})") from exc
    merged, _notes = sm.compose_settings_three_way(base_d, ours_d, theirs_d)
    merged = sm.apply_settings_overrides(merged, sm.load_settings_overrides(tree))
    arriving = _arriving_with(tree, target) if tree and target else None
    problems = sm.verify_composed_settings(merged, theirs_d, tree, arriving=arriving)
    if problems:
        raise MergeConflict("settings policy refused: " + "; ".join(problems))
    serialized = sm._dump_like_settings(merged, ours.decode("utf-8"))
    return ("stage", None) if serialized == ours else ("merge", serialized)


def _arriving_with(tree: str, target: str):
    """The vouch `verify_composed_settings` takes: a hook script absent from the
    tree's disk is still written by this fast-forward when `target` holds it.
    GRIP#8454 bound hooks/red-check-merge-gate.py and added the file in one
    commit; the policy checked the disk before the fast-forward that writes
    the file, refused, and the shared tree stuck (2026-10-27). A path outside
    the tree, or one `target` does not hold, is still missing."""
    def arriving(script: Path) -> bool:
        rel = os.path.relpath(script, tree)
        if rel.startswith(".."):
            return False
        return _git(tree, "cat-file", "-e", f"{target}:{Path(rel).as_posix()}").returncode == 0
    return arriving


def _combine(path: str, base: bytes, ours: bytes, theirs: bytes) -> tuple[str, bytes | None]:
    """Merge both sides; a REGENERATED file that clashes takes main's copy instead."""
    try:
        merged = merge_three(path, base, ours, theirs)
    except MergeConflict:
        if path not in REGENERATED:
            raise
        return "replace", theirs
    # Main's change was already in the local copy (a ledger snapshot committed from
    # this tree): nothing to write, so the file is left alone, not rewritten.
    return ("stage", None) if merged == ours else ("merge", merged)


def merge_three(path: str, base: bytes, ours: bytes, theirs: bytes) -> bytes:
    """Combine edits made to different parts; raise MergeConflict on any overlap."""
    if b"\0" in base or b"\0" in ours or b"\0" in theirs:
        raise MergeConflict("binary file changed on both sides")
    if path.endswith(".json"):
        return merge_json_text(base, ours, theirs)
    merged, conflicts = _merge_lines(base, ours, theirs)
    if conflicts == 0:
        return merged
    if path.endswith(".jsonl"):
        return union_appended_rows(merged, ours)
    raise MergeConflict(f"both sides changed the same lines ({conflicts} place(s))")


def merge_json_text(base: bytes, ours: bytes, theirs: bytes) -> bytes:
    """Three-way merge of a JSON file by key, written back in the local copy's style.

    A line merge cannot be trusted here: the /model picker rewrites the whole live
    settings.json in its own order, so its lines no longer line up with HEAD's even
    where the content agrees, and a line-level result need not even parse.
    """
    try:
        sides = [json.loads(side.decode("utf-8")) for side in (base, ours, theirs)]
    except ValueError as exc:  # UnicodeDecodeError is a ValueError too
        raise MergeConflict(f"one side is not valid JSON ({type(exc).__name__})") from exc
    return _dump_like(merge_values(*sides), ours.decode("utf-8"))


def merge_values(base, ours, theirs, where: str = "$"):
    """Three-way merge of decoded JSON: objects by key, everything else whole."""
    if _same(ours, theirs) or _same(theirs, base):
        return ours
    if _same(ours, base):
        return theirs
    if isinstance(base, dict) and isinstance(ours, dict) and isinstance(theirs, dict):
        return _merge_objects(base, ours, theirs, where)
    raise MergeConflict(f"both sides changed {where}")


def _merge_objects(base: dict, ours: dict, theirs: dict, where: str) -> dict:
    merged = {}
    # The local key order first, then keys only main added. A key both sides
    # deleted appears in neither list, so it stays deleted.
    for key in [*ours, *(k for k in theirs if k not in ours)]:
        value = merge_values(base.get(key, _ABSENT), ours.get(key, _ABSENT),
                             theirs.get(key, _ABSENT), f"{where}.{key}")
        if value is not _ABSENT:
            merged[key] = value
    return merged


def _same(left, right) -> bool:
    """JSON equality. Python's == calls true equal to 1 and 1.0 equal to 1."""
    if left is _ABSENT or right is _ABSENT:
        return left is right
    return _canonical(left) == _canonical(right)


def _canonical(value) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _dump_like(value, sample: str) -> bytes:
    """Serialise the way the local copy was written: same indent, same final newline."""
    found = _INDENT.search(sample)
    indent = None
    if found:
        indent = found.group(1) if "\t" in found.group(1) else len(found.group(1))
    text = json.dumps(value, indent=indent, ensure_ascii=False)
    return (text + "\n" if sample.endswith("\n") else text).encode("utf-8")


def _merge_lines(base: bytes, ours: bytes, theirs: bytes) -> tuple[bytes, int]:
    """git's own line merge: the result, and how many places conflicted."""
    with tempfile.TemporaryDirectory(prefix="grip-tree-sync-") as scratch:
        files = []
        for label, data in (("ours", ours), ("base", base), ("theirs", theirs)):
            file = Path(scratch, label)
            file.write_bytes(data)
            files.append(str(file))
        done = subprocess.run(
            ["git", "merge-file", "-p", "--diff3",
             "-L", "ours", "-L", "base", "-L", "theirs", *files],
            capture_output=True, timeout=60,
        )
    if done.returncode > 127:  # git merge-file exits negative (here 255) on error
        raise MergeConflict("git merge-file could not compare the three versions")
    return done.stdout, done.returncode


def union_appended_rows(merged: bytes, ours: bytes) -> bytes:
    """Settle a JSONL conflict in which both sides only ADDED rows at the same place.

    Both sets of rows are kept, the local ones first, and a row main added that the
    local copy already holds (compared as JSON, not as text) is not added twice: a
    ledger snapshot committed from this very tree is the common case. A hunk where
    either side changed or removed an existing row is still a conflict.
    """
    present = {_row_key(row) for row in ours.splitlines()}

    def settle(hunk: re.Match) -> bytes:
        if hunk.group(2):
            raise MergeConflict("both sides changed the same rows")
        new = [row for row in hunk.group(3).splitlines(keepends=True)
               if _row_key(row) not in present]
        return hunk.group(1) + b"".join(new)

    settled = _CONFLICT.sub(settle, merged)
    if _LEFTOVER_MARKER.search(settled):
        raise MergeConflict("conflict markers left that are not a pure append")
    return settled


def _row_key(row: bytes) -> str:
    try:
        return _canonical(json.loads(row))
    except ValueError:
        return row.decode("utf-8", "replace").strip()


# ---------------------------------------------------------------- planning

def plan(tree: str, target: str, path: str) -> Plan:
    """Read the three versions of one path and decide what it becomes. Changes nothing."""
    step = Plan(path)
    try:
        step.head_entry = _entry(tree, "HEAD", path)
        step.target_entry = _entry(tree, target, path)
        step.skip_worktree = _flagged_skip_worktree(tree, path)
        step.ours = _working(tree, path)
        step.base = _content(tree, "HEAD", path) if step.head_entry else None
        step.theirs = _content(tree, target, path) if step.target_entry else None
        use_policy = path == "settings.json" and _is_shared_tree(tree)
        step.kind, step.merged = decide(
            path, step.base, step.ours, step.theirs,
            use_settings_policy=use_policy, tree=tree, target=target)
    except MergeConflict as exc:
        step.kind, step.why = "block", str(exc)
    return step


# ---------------------------------------------------------------- applying

def resolve(tree: str, target: str, paths: list[str]) -> int:
    """Plan every path; if none blocks, stage, fast-forward and write back."""
    plans = [plan(tree, target, path) for path in paths]
    blocked = [step for step in plans if step.kind == "block"]
    for step in blocked:
        print(f"blocked\t{step.path}\t{step.why}")
    if blocked:
        return EXIT_BLOCKED
    moving = [step for step in plans if step.kind in ("stage", "merge", "replace")]
    try:
        _fast_forward(tree, target, moving)
    except RuntimeError as exc:
        print(f"error\t{exc}")
        return EXIT_FAILED
    for step in moving:
        print(_finish(tree, step))
    return EXIT_MERGED


def _fast_forward(tree: str, target: str, moving: list[Plan]) -> None:
    """Stage main's blob for each file, then fast-forward; undo the staging on failure."""
    if _git(tree, "merge-base", "--is-ancestor", "HEAD", target).returncode != 0:
        raise RuntimeError("not a fast-forward, and staging main's blobs only works for one")
    staged: list[Plan] = []
    try:
        for step in moving:
            staged.append(step)  # before the first change, so a half-done step is undone too
            if step.skip_worktree:
                _git_ok(tree, "update-index", "--no-skip-worktree", "--", step.path)
            _git_ok(tree, "update-index", "--cacheinfo", f"{step.target_entry},{step.path}")
        # --no-autostash: the stash stack is shared, and rebase.autostash is set on ~/.grip.
        _git_ok(tree, "merge", "--ff-only", "--no-autostash", "--quiet", target)
    except RuntimeError:
        _unstage(tree, staged)
        raise


def _unstage(tree: str, staged: list[Plan]) -> None:
    for step in staged:
        _git(tree, "update-index", "--cacheinfo", f"{step.head_entry},{step.path}")
        if step.skip_worktree:
            _git(tree, "update-index", "--skip-worktree", "--", step.path)


def _finish(tree: str, step: Plan) -> str:
    """Write the merged copy if there is one, then restore the skip-worktree bit."""
    said = f"staged\t{step.path}"
    if step.kind in ("merge", "replace"):
        said = _write_merged(tree, step)
    if step.skip_worktree:
        _git(tree, "update-index", "--skip-worktree", "--", step.path)
    return said


def _write_merged(tree: str, step: Plan) -> str:
    file = Path(tree, step.path)
    now = file.read_bytes() if file.is_file() else None
    kind, data = step.kind, step.merged
    if now != step.ours:
        # Something wrote to the file while the sync ran (a launchd agent appending
        # to a ledger, say). Never overwrite bytes the merge did not see: merge again.
        use_policy = step.path == "settings.json" and _is_shared_tree(tree)
        try:
            kind, data = decide(
                step.path, step.base, now, step.theirs, use_settings_policy=use_policy, tree=tree)
        except MergeConflict as exc:
            return (f"left\t{step.path}\tchanged during the sync and no longer combines "
                    f"with main ({exc}); kept as it is")
        if kind not in ("merge", "replace"):
            return f"staged\t{step.path}"
    _replace(file, data)
    return f"{'replaced' if kind == 'replace' else 'merged'}\t{step.path}"


def _replace(file: Path, data: bytes) -> None:
    """Atomic write that keeps the permission bits (settings.json is read live)."""
    mode = file.stat().st_mode & 0o7777 if file.exists() else 0o644
    handle, scratch = tempfile.mkstemp(dir=file.parent, prefix=f".{file.name}.", suffix=".tree-sync")
    try:
        with os.fdopen(handle, "wb") as out:
            out.write(data)
        os.chmod(scratch, mode)
        os.replace(scratch, file)
    except BaseException:
        Path(scratch).unlink(missing_ok=True)
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Fast-forward a GRIP tree over files changed both locally and on main.")
    parser.add_argument("--tree", required=True, help="the working tree to fast-forward")
    parser.add_argument("--target", default="origin/main", help="the ref to fast-forward to")
    args = parser.parse_args(argv)
    paths = [line for line in sys.stdin.read().splitlines() if line.strip()]
    try:
        return resolve(args.tree, args.target, paths)
    except RuntimeError as exc:
        print(f"error\t{exc}")
        return EXIT_FAILED


if __name__ == "__main__":
    sys.exit(main())
