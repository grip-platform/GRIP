#!/usr/bin/env python3
"""
PostToolUse hook: Documentation drift auto-fixer.

Fires on Write/Edit to skills/*/SKILL.md, agents/*.md, commands/*.md,
rules/*.md, hooks/*.py or modes/definitions/*.yaml. When inventory changes,
the hook DIRECTLY UPDATES the counts in the CLAUDE.md files and the marked
stats line of the checkout that CONTAINS the written file. No systemMessage
instruction, no advisory — JIT auto-fix.

Architecture:
- PostToolUse (after Write/Edit completes successfully)
- Resolves the output root FROM THE WRITTEN FILE (git rev-parse
  --show-toplevel), never from $CLAUDE_DIR — see resolve_output_root()
- Counts files via lib/asset_counter.py (shared canonical counter; inside a
  git checkout it counts TRACKED files only, so the line can equal main's).
  A worktree of this same repository is counted with its OWN copy of that
  counter, so its counting rules apply, never the shared tree's; any other
  repository keeps the hook's own copy, and its code never runs here (see
  counter_for()). Anchor: tests/test_doc_drift_hook_root.py.
- Updates counts in-place via regex substitution
- Runs code only from its own repository: a checkout's counter is loaded only
  when it shares this tree's git common directory (a worktree of it)
- Reports what it fixed via systemMessage (confirmation, not instruction)

Files auto-updated, all relative to the resolved root:
- skills/CLAUDE.md: "NNN skills organised" (either spelling)
- agents/CLAUDE.md: "NN specialised agents" (either spelling)
- modes/CLAUDE.md: "NN core operating modes"
- rules/system-capabilities.md: the whole marked stats line
- README.md: the per-category grip-stats marker lines

Never the root CLAUDE.md or .claude/CLAUDE.md. Both are on the merge-authority
list (BOOTSTRAP_EXCEPTED_PATHS in lib/agora_verdict_router.py), and a skill
write triggers this hook, so every new-skill PR that committed the hook's
rewrite of .claude/CLAUDE.md was held for a hand merge. Both files now point at
the stats line and carry no count (GRIP#9310, V>>'s choice of 2026-11-24).
Anchor: tests/test_doc_drift_hook_counts.py.

The stats line is the repo's most-referenced self-description — CLAUDE.md,
.claude/CLAUDE.md and AGENTS.md all point at it as "the live line". This hook
used to hand-patch only `Skills:` and `Agents:` on it, so Commands, Rules and
Hooks were stale BY CONSTRUCTION (measured 2026-08-18: Commands 97 claimed vs
389 real, Rules 47 vs 62, Hooks 88 vs 108). fix_capabilities_line() now
rebuilds the entire marked line from count_inventory(), so a new asset category
is picked up without another edit here. Falsifier: if the line ever drifts again
while this hook is wired, the counter and the hook have diverged — fix the
counter, do not reintroduce per-field patching.

Version: 3.1.0
"""

import hashlib
import importlib.util
import os
import re
import subprocess
import sys
from pathlib import Path
from types import ModuleType

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
from hook_utils import parse_hook_input, respond, extract_tool_info
import asset_counter  # the canonical counter; counter_for() may count a worktree with its own copy
from claude_dir import get_claude_dir


def resolve_output_root(file_path: str) -> Path | None:
    """The checkout that CONTAINS the written file — the only tree this hook
    may write into.

    History, because each step was a real defect. The root was first a
    hardcoded ``Path.home() / ".claude"``, then ``get_claude_dir()`` —
    $CLAUDE_DIR, else $HOME/.claude. Both name the SHARED tree that launchd
    agents execute from (rules/shared-tree-is-not-canonical.md): ~/.claude is
    a symlink onto ~/.grip, and nothing in GRIP exports CLAUDE_DIR for a
    worktree. So a skill written inside a git WORKTREE made this hook
    regenerate the shared tree's stats line from the shared tree's own
    inventory — leaving the worktree's artefact stale and dirtying a tree
    another session was using. The numbers were self-consistent, which is why
    nothing complained. Measured 2026-10-01: rules/system-capabilities.md was
    one of the two files blocking bin/grip-tree-sync on the shared tree, and
    main had changed it in 31 commits since 1 September.

    The written file names its own tree: ``git rev-parse --show-toplevel``
    from its directory. A file outside any checkout — a GRIP install that is
    not a clone — falls back to the configured root ONLY when it lives under
    it; otherwise there is nothing to update and the hook does nothing.
    Anchor: tests/test_doc_drift_hook_root.py.

    git prints the path as bytes: UTF-8 on Windows, and the file system's own
    bytes on POSIX. ``os.fsdecode`` reads them the way Python's own path calls
    do. ``text=True`` decoded them with the locale, cp1252 on Windows, so a
    checkout in a folder named "café" resolved to a folder that does not
    exist. Anchor: tests/test_doc_drift_hook_encoding.py.
    """
    target = Path(file_path)
    start = target if target.is_dir() else target.parent
    try:
        out = subprocess.run(
            ["git", "-C", str(start), "rev-parse", "--show-toplevel"],
            capture_output=True, timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        out = None
    top = os.fsdecode(out.stdout).strip() if out is not None and out.returncode == 0 else ""
    if top:
        return Path(top)
    configured = get_claude_dir()
    try:
        target.resolve().relative_to(configured.resolve())
    except ValueError:
        return None
    return configured


_HOOK_TREE = Path(__file__).resolve().parent.parent


def _git_common_dir(path: Path) -> Path | None:
    """The repository a checkout belongs to; a worktree shares its main tree's.

    git prints the path relative to ``path`` for a main tree and absolute for a
    linked worktree; joining it to ``path`` gives the same answer for both on
    any git, where `--path-format=absolute` needs git 2.31 (AGORA on GRIP#9633).
    """
    try:
        out = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "--git-common-dir"],
            capture_output=True, timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    text = os.fsdecode(out.stdout).strip()
    return (path / text).resolve() if out.returncode == 0 and text else None


# The functions run() calls on a counter.
_COUNTER_API = ("count_inventory", "fix_capabilities_line", "fix_marked_count_lines")


class CounterUnavailable(Exception):
    """The checkout's own counter cannot be used: git could not place the
    checkout, or its counter did not load or lacks a function the hook calls."""


def _load_counter(candidate: Path) -> ModuleType:
    """Import ``candidate`` under a name of its own and check it has the functions
    run() calls. A counter that fails either way leaves nothing in sys.modules."""
    name = "asset_counter_of_" + hashlib.sha1(str(candidate.resolve()).encode()).hexdigest()[:12]
    try:
        spec = importlib.util.spec_from_file_location(name, candidate)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    except Exception as exc:
        sys.modules.pop(name, None)
        raise CounterUnavailable(f"{type(exc).__name__}: {exc}") from exc
    missing = [fn for fn in _COUNTER_API if not callable(getattr(module, fn, None))]
    if missing:
        sys.modules.pop(name, None)
        raise CounterUnavailable("it has no " + ", ".join(missing))
    return module


def counter_for(root: Path) -> ModuleType:
    """The asset counter whose counting rules match ``root``.

    The hook runs from the shared tree, so the counter it imports holds the
    shared tree's rules. A worktree whose rules differ, through an unmerged
    change or main moving ahead of the shared tree, was recounted with the
    wrong ones: on 2026-11-24 a worktree's agent count went from 43 back to
    44. A checkout of this same repository (the same git common directory) is
    counted with its own lib/asset_counter.py. Any other checkout keeps the
    imported counter: running another repository's code from a hook is not
    this hook's to do. CounterUnavailable means nothing is rewritten: the
    checkout's counter did not load or lacks a function, or git could not say
    which repository the checkout belongs to, a case that used to fall back to
    the shared tree's rules without a word (AGORA on GRIP#9633).
    """
    candidate = root / "lib" / "asset_counter.py"
    if not candidate.is_file() or candidate.resolve() == Path(asset_counter.__file__).resolve():
        return asset_counter
    common = _git_common_dir(root)
    if common is None:
        raise CounterUnavailable("git could not say which repository it belongs to")
    if common != _git_common_dir(_HOOK_TREE):
        return asset_counter
    return _load_counter(candidate)


def update_file(path, patterns):
    """Apply regex substitutions to a file. Returns number of changes.

    The file is read and written as UTF-8 bytes, so the counts are the only
    bytes that change. ``read_text()`` decoded with the locale's encoding,
    which is cp1252 on Windows. The root CLAUDE.md, then the first file in the
    list, holds U+274C (bytes e2 9d 8c), and cp1252 has no character for 0x9d,
    so on Windows the hook raised there and updated nothing. ``write_text()``
    also turned every LF into CRLF on Windows.
    Anchor: tests/test_doc_drift_hook_encoding.py.
    """
    if not path.exists():
        return 0
    original = path.read_bytes().decode("utf-8")
    content = original
    for pattern, replacement in patterns:
        content = re.sub(pattern, replacement, content)
    if content != original:
        path.write_bytes(content.encode("utf-8"))
        return 1
    return 0


def _forward_slashes(file_path, windows=None):
    """``file_path`` with each backslash read as a separator on Windows only.

    On Windows a backslash is always a separator, because a file name cannot
    hold one. On POSIX it is an ordinary file-name character, so the path is
    returned unchanged. ``windows`` defaults to the platform this runs on; a
    test can set it to check either platform's rule.
    """
    if windows is None:
        windows = os.name == "nt"
    return file_path.replace("\\", "/") if windows else file_path


def is_inventory_file(file_path, windows=None):
    """True if a Write/Edit to file_path can change an inventory count.

    One branch per category counted by lib/asset_counter.py, and the two must
    stay in step: a category the counter reports but this predicate ignores is
    stale BY CONSTRUCTION, because no edit to it can ever trigger the auto-fix.

    Rules and hooks were exactly that. They sat on the stats line, the counter
    computed them, and this predicate matched neither — so on 2026-08-18 they
    were the two fields measured wrong (Rules 62 claimed vs 61 real, Hooks 109
    vs 110) while skills, agents, commands and modes were self-healing.

    Claude Code on Windows passes the path with backslashes, and no branch
    below matched one, so on Windows the hook never ran. The path is read
    through ``_forward_slashes`` first. Anchor: tests/test_doc_drift_hook_root.py.
    """
    file_path = _forward_slashes(file_path, windows)
    is_md = file_path.endswith(".md") and "CLAUDE.md" not in file_path
    return (
        ("/skills/" in file_path and "SKILL.md" in file_path) or
        ("/agents/" in file_path and is_md) or
        ("/commands/" in file_path and is_md) or
        ("/rules/" in file_path and is_md) or
        ("/hooks/" in file_path and file_path.endswith(".py")) or
        ("/modes/definitions/" in file_path and file_path.endswith(".yaml"))
    )


def _count_patterns(skills, agents, modes):
    """The regex substitutions per CLAUDE.md file: (relative path, patterns).

    Exact top-level skill count (matches grip-stats.sh; nested SKILL.md != skills).
    The root CLAUDE.md and .claude/CLAUDE.md are absent on purpose: both are on
    the merge-authority list, so this hook must never write them (GRIP#9310).
    """
    return (
        # Both of these accept either spelling and write back the one the file
        # already uses (GRIP#9310). Each pattern had one spelling and its file
        # the other: skills/CLAUDE.md says "organised" and agents/CLAUDE.md says
        # "specialized", so neither line ever matched and both counts went stale.
        ("skills/CLAUDE.md", [(r"~?\d+ skills (organi[sz]ed)", rf"{skills} skills \g<1>")]),
        ("agents/CLAUDE.md", [(r"\d+ (speciali[sz]ed) agents", rf"{agents} \g<1> agents")]),
        ("modes/CLAUDE.md", [(r"\d+ core operating modes", f"{modes} core operating modes")]),
    )


def auto_fix_counts(root, skills, agents, modes, counter=None):
    """Update the CLAUDE.md files and the marked stats line under ``root``.
    ``counter`` is the module from counter_for(); the imported one by default.
    Returns the number of files changed."""
    counter = counter or asset_counter
    files_changed = 0
    for relpath, patterns in _count_patterns(skills, agents, modes):
        files_changed += update_file(root / relpath, patterns)
    if counter.fix_capabilities_line(root):
        files_changed += 1
    return files_changed


def run(tool_name, file_path):
    """The hook's whole behaviour, as a function of its two inputs.

    Returns the confirmation message, or None when there is nothing to say:
    not a Write/Edit, not an inventory file, no tree to write into, or no
    count changed. main() only parses stdin and forwards the answer, so a
    test can drive this with a path and read where the bytes landed.
    """
    if tool_name not in ("Write", "Edit") or not is_inventory_file(file_path):
        return None
    root = resolve_output_root(file_path)
    if root is None:
        return None
    try:
        counter = counter_for(root)
    except CounterUnavailable as exc:
        return (f"[DOC-DRIFT] {root / 'lib' / 'asset_counter.py'} could not be used ({exc}), "
                "so no count was changed")

    counts = counter.count_inventory(root)
    skills, agents, modes = counts["skills"], counts["agents"], counts["modes"]
    files_changed = auto_fix_counts(root, skills, agents, modes, counter)

    # README reference lines self-update via per-category grip-stats markers
    # (covers commands too, which auto_fix_counts does not track).
    if counter.fix_marked_count_lines(root / "README.md", counts):
        files_changed += 1

    if files_changed > 0:
        return (
            f"[DOC-DRIFT AUTO-FIX] Updated {files_changed} file(s) under {root}: "
            f"skills=~{(skills // 10) * 10}, agents={agents}, modes={modes}"
        )
    return None


def main():
    hook_input = parse_hook_input()
    if not hook_input:
        respond()
        return

    tool_name, tool_input, _ = extract_tool_info(hook_input)
    message = run(tool_name, tool_input.get("file_path", ""))
    if message:
        respond(message)
    else:
        respond()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        respond()
