#!/usr/bin/env python3
"""Canonical GRIP inventory counter — the single source of truth for asset counts.

Both ``scripts/grip-stats.sh`` and ``hooks/doc-drift-hook.py`` call into this
module so the six inventory counts (skills, agents, commands, rules, hooks,
modes) are computed in exactly ONE place. Before this module the two callers
each carried their own copy of the counting logic and they had already drifted:
the hook counted modes via ``*.yaml`` while grip-stats.sh counted every entry in
``modes/definitions/`` (a stray ``README``/``CLAUDE.md`` there would inflate the
mode count). Centralising removes that whole class of count drift.

This replaces an earlier draft whose API (``count_all`` → playbooks/workers/
presets/guards) used deprecated terminology and a different, never-imported
counting scheme. No code imported it (verified repo-wide); the canonical six
plain-language categories below are what GRIP actually reports.

Counting rules (each EXCLUDES per-directory ``CLAUDE.md`` index files):

  skills    skills/*/SKILL.md          top-level only. Nested SKILL.md (under
                                       skills/_archived/ or a sub-skill dir) is
                                       NOT a canonical skill and is not counted.
  agents    agents/**/*.md minus NON_AGENT_FILES: CLAUDE.md, and
                                       _base-protocol.md, the shared gate
                                       protocol the agents link to. Neither has
                                       the frontmatter Claude Code loads an
                                       agent from, so neither is an agent.
                                       Subfolders count, because Claude Code
                                       loads agents/bridge/ too (GRIP#9526).
  commands  commands/*.md minus CLAUDE.md
  rules     rules/*.md minus CLAUDE.md
  hooks     hooks/*.py
  modes     modes/definitions/*.yaml   *.yaml only — a CLAUDE.md/README in
                                       definitions/ is not a mode.

Every category additionally excludes matches that RESOLVE OUTSIDE the install
root, because what they point at exists on some machines and not others — which
made the total machine-dependent and is why the committed stats line used to
conflict on merge. ``_is_inside`` is the check; the eleven entries that drove it
are described below.

Agents are also counted BY TIER (``count_agents_by_tier``). Each agent's
frontmatter ``tier:`` is 1 to 4, or the label itself: core, specialist,
infrastructure, experimental. The tier count reads the same files as the
``agents`` category, so the tiers always add up to the agent count, and an
agent with no tier lands in "unclassified" instead of vanishing.
``scripts/generate-reference-docs.py`` groups docs/AGENTS.md with the same
reader, ``agent_tier``.

Inside a git checkout, only files GIT TRACKS are inventory (``_tracked_paths``).
A working install carries more than its index — the gitignored
``rules/session-context.md``, a hook another session is drafting, a release
file nobody committed — and every one of those used to count, so the stats
line this counter rewrote in the shared tree could never equal the line on
main. Measured 2026-10-01: that mismatch was one of the two files blocking
``bin/grip-tree-sync`` on the shared tree. A tree that is not a checkout counts
what is on disk, which is the only inventory it has.

THE ELEVEN SYMLINKS UNDER ``skills/`` — what is verified, and what is not. The
separation is deliberate: a docstring that guesses here gets acted on, and both
possible actions are destructive in opposite directions.

  VERIFIED. They are tracked in git at mode ``120000``, pointing at
  ``~/.agents/skills/<name>``. They landed in ``2660f9aab`` (2026-09-06), a
  1,280-file strict-loader sweep whose message never mentions them. They
  contribute ZERO tracked ``SKILL.md`` paths — 316 are tracked and these eleven
  are bare symlink blobs. ``commands/<name>.md`` identifies the underlying
  skills as ``source: codex`` / ``codex-plugin``, with a ``source-path`` under
  ``${CODEX_HOME}/skills`` or the Codex plugin cache — a different location from
  the one the symlinks point at.

  NOT ESTABLISHED. That GRIP requires them, or that tracking them was intended.
  Two earlier revisions of this file each asserted a purpose — first that
  plugins installed them, then that they were committed deliberately for
  strict-loader interoperability — the second inferred from nothing more than
  co-location in one commit among 1,280 files. Codex raised it on GRIP#5697 and
  was right: one claim leads a maintainer toward ``git rm``, the other toward
  preserving possibly-accidental external links. Whether they belong in the
  index is a decision for someone with the history, not for this counter.

Usage (CLI — the stable contract grip-stats.sh depends on):
  python3 lib/asset_counter.py          # print the canonical stats line
  python3 lib/asset_counter.py --json   # emit the counts dict as JSON
  python3 lib/asset_counter.py --tiers  # also print the agents by tier; with
                                        # --json, under "agents_by_tier"
  python3 lib/asset_counter.py --fix    # rewrite the marked stats line in
                                        # rules/system-capabilities.md and the
                                        # per-category grip-stats marker lines
                                        # in README.md

API:
  from asset_counter import count_agents_by_tier, count_inventory
  counts = count_inventory()            # {"skills": N, "agents": N, ...}
  tiers = count_agents_by_tier()        # {"core": N, ..., "unclassified": N}
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))
from claude_dir import get_claude_dir  # noqa: E402

# The HTML-comment marker on the canonical stats line in
# rules/system-capabilities.md. Kept identical to grip-stats.sh's historical
# token so old and new fixers target the same line.
MARKER = "<!-- grip-stats -->"

# Files in agents/ that are not agents: the directory's CLAUDE.md index, and
# _base-protocol.md, the gate protocol the agent definitions link to ("All
# gates from _base-protocol.md apply"). Neither has frontmatter, which is where
# Claude Code reads an agent's name and description, and the harness's agent
# list showed neither on 2026-11-24. scripts/generate-reference-docs.py and
# lib/skill_reachability.py use the same names and AGENT_GLOB, so all three list
# the same files. Each lists agents/ on its own; their totals are checked against
# this module by tests/test_skill_reachability_kinds.py and the generator's asserts.
NON_AGENT_FILES: frozenset[str] = frozenset({"CLAUDE.md", "_base-protocol.md"})
# Agents are counted the way Claude Code loads them: every markdown file under
# agents/, subfolders included (the two A2A bridge agents live in
# agents/bridge/). V>> chose this on 2026-11-25 (GRIP#9526), so the count is
# the set a session can dispatch. tests/test_asset_counter.py checks that
# every counted file declares the name and description loading needs.
AGENT_GLOB = "**/*.md"
_INDEX_FILE: frozenset[str] = frozenset({"CLAUDE.md"})
_NOTHING: frozenset[str] = frozenset()

# Inventory categories: (key, subpath, glob, file names to exclude).
# Declarative so adding a category is a one-line change, not new logic.
_CATEGORIES: tuple[tuple[str, str, str, frozenset[str]], ...] = (
    ("skills", "skills", "*/SKILL.md", _NOTHING),
    ("agents", "agents", AGENT_GLOB, NON_AGENT_FILES),
    ("commands", "commands", "*.md", _INDEX_FILE),
    ("rules", "rules", "*.md", _INDEX_FILE),
    ("hooks", "hooks", "*.py", _NOTHING),
    ("modes", "modes/definitions", "*.yaml", _NOTHING),
)
_CATEGORY = {key: (subpath, pattern, exclude) for key, subpath, pattern, exclude in _CATEGORIES}

# An agent's frontmatter ``tier:`` value -> its plain label. The value is the
# tier's number (1 is core) or the label itself. Each name is written once, so
# the numbers and the labels cannot drift. docs/AGENTS.md is grouped in
# TIER_ORDER.
_TIER_NAMES: tuple[str, ...] = ("core", "specialist", "infrastructure", "experimental")
TIER_LABELS: dict[str, str] = {
    **{str(number): name for number, name in enumerate(_TIER_NAMES, start=1)},
    **{name: name for name in _TIER_NAMES},
}
TIER_ORDER: tuple[str, ...] = (*_TIER_NAMES, "unclassified")
_TIER_KEY = re.compile(r"^tier:\s*(.*)$")
# A YAML comment starts at a "#" with white space before it.
_YAML_COMMENT = re.compile(r"\s+#.*$")


def _is_inside(path: Path, root_resolved: Path) -> bool:
    """True when ``path`` really lives under ``root_resolved``.

    ``skills/`` holds eleven tracked symlinks pointing outside the root; the
    module docstring records what is verified about them and what is not.

    They contribute ZERO tracked
    ``SKILL.md`` paths (316 are tracked; the eleven are bare symlink blobs), and
    whether the glob matches through one depends on whether that machine has the
    corpus at all. Measured 2026-09-14 — the shared install globbed 328 raw and
    kept 317, dropping exactly 11; a clean checkout globbed 316 and dropped none,
    because there the links DANGLE and the glob never reaches a ``SKILL.md``
    through them.

    The dangling case is why CI green does not exercise this filter: on a fresh
    clone it drops nothing. What proves it are the two unit tests below, which
    build real symlinks whose targets exist.

    ``root_resolved`` must ALREADY be resolved. ``~/.claude`` is itself a symlink
    to ``~/.grip`` and ``get_claude_dir()`` returns that symlink path, so
    comparing against an unresolved root would place every match outside the root
    and return zero for all six categories on the default call path.
    """
    try:
        path.resolve().relative_to(root_resolved)
        return True
    except ValueError:
        return False


def _tracked_paths(root: Path) -> frozenset | None:
    """The paths git tracks under the six inventory directories, joined to
    ``root`` exactly as the globs join them; None when ``root`` is not itself
    a git checkout, or git cannot answer.

    ``root`` must be the TOPLEVEL, not a directory inside some other
    repository: a temporary fixture that happens to sit under a checkout would
    otherwise read that checkout's index and count zero of its own files. One
    ``ls-files`` call for all six categories — a hook runs this after every
    inventory Write, so the cost is one subprocess, not six.

    The top-level path is decoded with ``os.fsdecode``, as Python's own path
    calls decode a path. ``text=True`` decoded it with the locale, cp1252 on
    Windows, so a checkout in a folder named "café" never matched its root,
    and every untracked draft was counted.
    """
    try:
        top = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--show-toplevel"],
            capture_output=True, timeout=10,
        )
        top_path = os.fsdecode(top.stdout).strip()
        if top.returncode != 0 or Path(top_path).resolve() != root.resolve():
            return None
        listed = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z", "--full-name", "--",
             *(subpath for _, subpath, _, _ in _CATEGORIES)],
            capture_output=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if listed.returncode != 0:
        return None
    names = listed.stdout.decode("utf-8", "surrogateescape").split("\0")
    return frozenset(root / name for name in names if name)


def _inventory_paths(
    root: Path, root_resolved: Path, subpath: str, pattern: str, exclude: frozenset[str],
    tracked: frozenset | None = None,
) -> Iterator[Path]:
    """Yield one category's matches that EXIST, live inside the root, and —
    inside a checkout — are tracked by git.

    One plain pass over this category's glob, O(matches), no membership scan.
    ``_count_category`` counts what this yields and ``count_agents_by_tier``
    reads it, so the two can never disagree about which files are inventory.

    Four filters, in cost order. Excluded names go first. Then existence,
    because a glob matches a symlink by its NAME and says nothing about whether
    the target is there: a link to a file that was deleted, or a pair of links
    pointing at each other, both match and both name nothing. Measured
    2026-09-14 on a fixture with one real hook — the counter returned 4.
    ``Path.exists()`` follows the link and returns False for both shapes,
    including the loop, where it does NOT raise. Then tracking, a set lookup
    (``tracked`` is None outside a checkout, so the filter is inert there).
    Then containment, which is the expensive one because it resolves.

    Existence is deliberately NOT folded into ``_is_inside``: that function
    answers where a path lives, and a broken link inside the root still lives
    inside the root. Two properties, two checks.
    """
    for path in (root / subpath).glob(pattern):
        if path.name in exclude:
            continue
        if not path.exists():
            continue
        if tracked is not None and path not in tracked:
            continue
        if _is_inside(path, root_resolved):
            yield path


def _count_category(
    root: Path, root_resolved: Path, subpath: str, pattern: str, exclude: frozenset[str],
    tracked: frozenset | None = None,
) -> int:
    """How many files ``_inventory_paths`` keeps for one category.

    Extracted so ``count_inventory`` holds no loop-nested comprehension.
    """
    return sum(1 for _ in _inventory_paths(root, root_resolved, subpath, pattern, exclude, tracked))


def count_inventory(claude_dir: Path | str | None = None) -> dict[str, int]:
    """Return the six canonical inventory counts.

    Pure: reads the filesystem and the git index, writes nothing.
    ``claude_dir`` overrides the install root (used by tests against a fixture
    tree); defaults to ``get_claude_dir()``.

    Counts what the install genuinely CONTAINS: a match resolving outside the
    root is a symlink to another project's file and is not GRIP inventory, and
    inside a checkout a file git does not track is a draft, a leftover or a
    runtime artefact — not inventory either. See ``_is_inside`` and
    ``_tracked_paths`` for why each distinction is load-bearing.
    """
    root = Path(claude_dir) if claude_dir is not None else get_claude_dir()
    root_resolved = root.resolve()
    tracked = _tracked_paths(root)
    return {
        key: _count_category(root, root_resolved, subpath, pattern, exclude, tracked)
        for key, subpath, pattern, exclude in _CATEGORIES
    }


def agent_tier(text: str) -> str:
    """The tier label an agent file's frontmatter declares, or "unclassified".

    Reads only the leading ``---`` block, so a ``tier:`` line in the body is
    prose, not a tier. The last ``tier:`` key wins. As in YAML, a comment
    after the value is dropped (``tier: 2 # specialist`` is 2), and then
    matching quotes. A value with no label (missing, a fifth tier) is
    "unclassified", so the agent stays visible in the count.
    """
    if not text.startswith("---"):
        return "unclassified"
    end = text.find("\n---", 3)
    if end == -1:
        return "unclassified"
    value = ""
    for line in text[3:end].splitlines():
        match = _TIER_KEY.match(line)
        if match:
            value = _YAML_COMMENT.sub("", match.group(1)).strip()
    if value[:1] in ("'", '"') and value[-1:] == value[:1]:
        value = value[1:-1]
    return TIER_LABELS.get(value.lower(), "unclassified")


def count_agents_by_tier(claude_dir: Path | str | None = None) -> dict[str, int]:
    """Count the agents ``count_inventory`` counts, by the tier each declares.

    The same files as the ``agents`` category (same glob, exclusions and
    filters), so the values always add up to ``count_inventory()["agents"]``.
    Every label in TIER_ORDER is a key, zeros included, so the shape is fixed.
    """
    root = Path(claude_dir) if claude_dir is not None else get_claude_dir()
    subpath, pattern, exclude = _CATEGORY["agents"]
    tiers = dict.fromkeys(TIER_ORDER, 0)
    for path in _inventory_paths(root, root.resolve(), subpath, pattern, exclude, _tracked_paths(root)):
        tiers[agent_tier(path.read_text(encoding="utf-8", errors="replace"))] += 1
    return tiers


def tiers_line(tiers: dict[str, int]) -> str:
    """Render the per-tier count: "Agents by tier: 8 core, 23 specialist, ...".

    The four named tiers always appear. "unclassified" appears only when an
    agent has no tier, because then it is something to fix.
    """
    shown = [f"{tiers.get(tier, 0)} {tier}" for tier in TIER_ORDER
             if tier != "unclassified" or tiers.get(tier)]
    return "Agents by tier: " + ", ".join(shown)


def stats_line(counts: dict[str, int]) -> str:
    """Render the canonical marker line for rules/system-capabilities.md."""
    return (
        f"Skills: {counts['skills']} | Agents: {counts['agents']} | "
        f"Commands: {counts['commands']} | Rules: {counts['rules']} | "
        f"Hooks: {counts['hooks']} | Modes: {counts['modes']} {MARKER}"
    )


def fix_capabilities_line(claude_dir: Path | str | None = None) -> bool:
    """Rewrite the marked stats line in rules/system-capabilities.md.

    Returns True if the file changed. The whole line CONTAINING the marker is
    replaced; a lambda replacement avoids re backreference interpretation.

    The file is read and written as UTF-8 bytes, so the stats line is the only
    thing that changes: ``read_text()`` and ``write_text()`` used the locale's
    encoding (cp1252 on Windows) and wrote CRLF there. Because nothing
    translates line endings any more, the pattern stops before a line's CR,
    so a file with CRLF endings keeps them.
    """
    root = Path(claude_dir) if claude_dir is not None else get_claude_dir()
    target = root / "rules" / "system-capabilities.md"
    if not target.exists():
        return False
    line = stats_line(count_inventory(root))
    text = target.read_bytes().decode("utf-8")
    new_text = re.sub(
        rf"^[^\r\n]*{re.escape(MARKER)}[^\r\n]*(?=\r?$)", lambda _m: line, text, flags=re.M
    )
    if new_text != text:
        target.write_bytes(new_text.encode("utf-8"))
        return True
    return False


def category_marker(key: str) -> str:
    """The per-category HTML-comment marker, e.g. ``<!-- grip-stats:agents -->``.

    The whole-line ``MARKER`` above rebuilds one canonical six-count stats line.
    This per-category variant opts a free-form prose line in to self-updating a
    SINGLE count — e.g. README's ``- [Agents Reference](docs/AGENTS.md) - 38
    agents``. HTML comments are invisible in rendered Markdown.
    """
    return f"<!-- grip-stats:{key} -->"


def fix_marked_count_lines(
    path: Path | str, counts: dict[str, int] | None = None
) -> bool:
    """Self-update per-category counts on lines opted in with a category marker.

    For each inventory KEY, a line shaped ``... <N> <key> <!-- grip-stats:KEY -->``
    has its ``<N>`` rewritten to ``counts[KEY]`` (the integer, the lowercase
    category word and the marker must be adjacent). Opt-in by construction: only
    lines carrying the literal marker are touched, so unrelated digits elsewhere
    in the file are never rewritten. Returns True if the file changed; a missing
    file returns False without raising. Read and written as UTF-8 bytes, for
    the reason ``fix_capabilities_line`` gives.
    """
    p = Path(path)
    if not p.exists():
        return False
    counts = counts if counts is not None else count_inventory()
    text = p.read_bytes().decode("utf-8")
    new_text = text
    for key, value in counts.items():
        pattern = rf"\d+(\s+{re.escape(key)}\s+{re.escape(category_marker(key))})"
        new_text = re.sub(pattern, lambda m, v=value: f"{v}{m.group(1)}", new_text)
    if new_text != text:
        p.write_bytes(new_text.encode("utf-8"))
        return True
    return False


def refresh_readme_markers(claude_dir: Path | str | None = None) -> bool:
    """Refresh the README's per-category grip-stats markers from count_inventory().

    Thin convenience wrapper the doc generators call after regenerating their
    canonical output, so each callsite need not re-derive the README path or
    re-import the fixer. Counts and README are read from the SAME root, so the
    rewrite always reflects that tree. Returns True if README.md changed.
    """
    root = Path(claude_dir) if claude_dir is not None else get_claude_dir()
    return fix_marked_count_lines(root / "README.md", count_inventory(root))


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    counts = count_inventory()
    tiers = count_agents_by_tier() if "--tiers" in args else None
    if "--json" in args:
        print(json.dumps(counts if tiers is None else {**counts, "agents_by_tier": tiers}, indent=2))
        return 0
    print(stats_line(counts))
    if tiers is not None:
        print(tiers_line(tiers))
    if "--fix" in args:
        root = get_claude_dir()
        updated = []
        if fix_capabilities_line():
            updated.append("rules/system-capabilities.md")
        if fix_marked_count_lines(root / "README.md", counts):
            updated.append("README.md")
        print(f"Updated {', '.join(updated)}" if updated else "No change needed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
