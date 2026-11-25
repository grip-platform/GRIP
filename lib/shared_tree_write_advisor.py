"""Advise when a session working elsewhere writes into the shared tree.

``~/.claude`` is a symlink onto ``~/.grip``, the shared working tree that
launchd agents execute from. rules/shared-tree-is-not-canonical.md, mandate 3:
leave it on main; feature work belongs in a git-crypt-safe worktree
(lib/grip-worktree.sh). A write that lands there from a session working
somewhere else is how that mandate breaks without anyone deciding to break it.

Measured 2026-10-01: one session copied the open PR GRIP#6886's files straight
into ~/.grip (bin/aide-ideate, bin/aide-morning and lib/learning_detector.py
edited; lib/dojo_*.py, hooks/dojo-tip-sessionstart.py and
scripts/daily-tip-channel.py untracked), and another edited
~/.grip/bin/grip-pr-monitor in place. Both blocked bin/grip-tree-sync from
keeping the shared tree level with main, so everything merged after 16:24 UTC
ran only where a process was restarted by hand.

``advise`` is the pure half: given the write's target, the session's working
directory and the shared tree, it decides whether to advise and says what. It
has no side effects and reads the filesystem only to resolve symlinks, which
the decision depends on because ~/.claude itself is one.
hooks/shared-tree-write-advisory.py is the impure half.

It fires when the target resolves inside the shared tree while the working
directory resolves outside it. It is silent when:

* the target is anywhere else, the session's own worktree included;
* the working directory is inside the shared tree, because a session run
  there deliberately is not working "somewhere else";
* the target is under one of RUNTIME_DIRS, which sessions write by design;
* the shared tree, the target or the working directory cannot be resolved.

A working directory that merely CONTAINS the shared tree, such as $HOME, does
not own it: a session launched from $HOME that writes into ~/.grip is still
landing work in the shared tree, so the advisory fires. Known residual: a
worktree nested INSIDE the shared tree (for example under .claude/worktrees/)
counts as being in the shared tree, so writes from it are silent. GRIP's own
worktrees live in ~/.grip-worktrees, outside it.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))
from claude_dir import get_claude_dir  # noqa: E402

__all__ = ["RUNTIME_DIRS", "advise", "shared_tree_root"]

# Top-level directories of the shared tree that sessions write by design, so a
# write there is runtime data rather than feature work. state, logs, data and
# cache come from the plan that commissioned this module. projects (Claude
# Code's auto-memory), plans (plan mode), drafts (/sneak) and sync (/sync) are
# written by design too; leaving them out would fire on every memory write and
# train the advisory to be ignored. tmp joined for the same reason: every
# session writes its landing record to tmp/fleet-landing, and those writes were
# 45 of the first 48 warnings logged on one machine (2026-10-05). Those five are
# gitignored by default, and tests/test_shared_tree_write_advisory_hook.py pins
# that, so this set cannot quietly grow to hide a source directory.
RUNTIME_DIRS = frozenset(
    {"state", "logs", "data", "cache", "projects", "plans", "drafts", "sync", "tmp"}
)

_REMEDY = (
    "bash lib/grip-worktree.sh add ~/.grip-worktrees/<name> "
    "feat/<branch> --base origin/main"
)


def shared_tree_root() -> str | None:
    """The shared tree: $CLAUDE_DIR, else $HOME/.claude, realpath'd.

    None when that does not resolve to an existing directory.
    """
    real = os.path.realpath(get_claude_dir())
    return real if os.path.isdir(real) else None


def _real(path: object, base: str | None = None) -> str | None:
    """The realpath of ``path``, joined to ``base`` first when relative.

    None for anything that is not a non-empty string, and for a relative path
    with no base to resolve it against.
    """
    if not isinstance(path, str) or not path.strip():
        return None
    expanded = os.path.expanduser(path)
    if base is not None and not os.path.isabs(expanded):
        expanded = os.path.join(base, expanded)
    if not os.path.isabs(expanded):
        return None
    return os.path.realpath(expanded)


def _within(path: str, root: str) -> bool:
    """True when ``path`` is ``root`` or lies beneath it, by whole components.

    A string prefix test would call ~/.grip-other part of ~/.grip.
    """
    try:
        return os.path.commonpath([path, root]) == root
    except ValueError:
        return False


def _is_runtime(target: str, shared: str) -> bool:
    """True when ``target`` sits under a top-level runtime directory."""
    first = os.path.relpath(target, shared).split(os.sep, 1)[0]
    return first in RUNTIME_DIRS


def _message(target: str, cwd: str, shared: str) -> str:
    """The advisory. Its first line stands alone as the operator's notice."""
    return "\n".join((
        f"[SHARED-TREE WRITE] {target} lands in the shared tree, but this "
        f"session works in {cwd}. Feature work belongs in a worktree "
        "(rules/shared-tree-is-not-canonical.md, mandate 3).",
        f"The shared tree ({shared}) is the checkout launchd agents execute "
        "from, so what is written there is what production runs.",
        "Measured 2026-10-01: one session copied the open PR GRIP#6886's "
        "files into ~/.grip and another edited ~/.grip/bin/grip-pr-monitor "
        "in place. Both blocked bin/grip-tree-sync, so everything merged "
        "after 16:24 UTC ran only where a process was restarted by hand.",
        f"Remedy: make the change in a worktree -- {_REMEDY}",
        "Advisory only: this write is not blocked.",
    ))


def advise(target_path: str, cwd: str, shared_tree: str) -> str | None:
    """The advisory for one write, or None when there is nothing to say."""
    shared = _real(shared_tree)
    if shared is None or not os.path.isdir(shared):
        return None
    where = _real(cwd)
    if where is None or _within(where, shared):
        return None
    target = _real(target_path, base=where)
    if target is None or not _within(target, shared):
        return None
    if _is_runtime(target, shared):
        return None
    return _message(target, where, shared)
