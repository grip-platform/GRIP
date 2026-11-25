#!/usr/bin/env python3
"""Which test files does CI actually run, and which does it reach by no route?

WHY THIS EXISTS. Every pytest invocation in .github/workflows/ names an explicit
file or reads a block file; there is no `pytest tests/` glob anywhere, and
.github/workflows/test-baseline-probe.yml records that invoking pytest with no
paths "collects the ENTIRE tree" as something it deliberately avoids. That is a
deliberate, measured choice -- see the narrowing comments in validate.yml, where
a 214-file candidate set gave 95 failures on a bare runner, 74 of them one
missing `cryptography` module.

The side effect nobody chose: a NEW test file is named by no workflow and listed
in no block, so it runs nowhere the moment it is written. GRIP#6379 shipped two
anchors that ran nowhere for exactly that reason, and one of its commit messages
claimed a call "cannot be removed silently" while nothing ran the guard.

WHY THIS CAN ACTUALLY FAIL, which is the whole design question. The obvious
shape -- compare a manifest against the glob that produced it -- is a tautology
for a list block, because the manifest and the glob are the same file. That is
shape 3 of rules/tests-must-be-able-to-fail.md, and
tests/test_unbound_sweep_block_stays_what_it_claims.py says so in its own
docstring, which is why it deliberately has no such test. This module compares
two genuinely independent things: the test files on disk, and the union of what
CI resolves. Either side can move without the other, so the comparison can
disagree.

WHY IT SHELLS OUT rather than reimplementing the block lists. scripts/
gate-test-block.sh is the resolver every CI job uses. Parsing config/test-blocks/
here would create a second copy that can drift from the one CI runs, and then
this module would confidently report on a set nobody executes. Invoking the real
resolver means the coverage view IS what CI runs, by construction.

THREE BUCKETS, because "not run by CI" is not one thing:

  covered  -- reached by some pull-request job
  excluded -- listed in a *-excluded.txt with a recorded reason it CANNOT run on
              a bare runner. Deliberate, documented, not actionable.
  orphan   -- in neither. The actionable set, and the only number worth acting on.

Collapsing the last two would report documented decisions as defects and make the
number unusable, which is how a detector becomes noise and then gets ignored.

THE COUNT GATES NOTHING; ONE NARROW CHECK DOES. `render()` is a report and must
never grow a pass/fail word; a test pins that. What can fail a build is
narrower, and V>> chose it on 2026-10-16 (GRIP#6423): a pull request that ADDS a
test file no CI job runs. `--fail-on-added FILE` judges only the files a pull
request adds, so the backlog of existing orphans can never trip it, and no pull
request is turned red over files it did not write. The report alone did not
stop the miss: on GRIP#8378 it printed `ORPHANS : 226` while that pull
request's new test ran nowhere.

It exits 0 when every added test file is run or excluded with a reason, 1 when
it names an added orphan, and 2 when the list cannot be read. The list is what
`git diff -z --name-only` prints: NUL-separated and never quoted. The default
output quotes a path with a non-ASCII character, and the quoted form matches no
file on disk, so such a test would pass unseen.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from collections.abc import Iterable
from dataclasses import dataclass, field
from itertools import chain, takewhile
from pathlib import Path

__all__ = ["Coverage", "classify", "render", "all_test_files", "blocks_referenced",
           "added_orphans"]

# A `.py` path as a workflow writes it: a run of characters that are not shell or
# YAML punctuation, ending in .py.
_PY_WORD = re.compile(r"""[^\s'"`;|&<>(){}\[\]$\\,=:*?]+?\.py(?![\w.-])""")
# A top-level `on:` key, plain or quoted, and a trigger that runs the workflow for
# a pull request. \b fails between "pull_request" and "_", so the
# pull_request_review_comment trigger does not count.
_ON_KEY = re.compile(r"""^(?:on|"on"|'on')\s*:""")
_PR_TRIGGER = re.compile(r"\bpull_request(?:_target)?\b")
# `./scripts/gate-test-block.sh <block-name>` as the workflows invoke it.
_BLOCK_RE = re.compile(r"gate-test-block\.sh\s+([A-Za-z0-9][A-Za-z0-9_-]*)")


@dataclass
class Coverage:
    """The classification. Counts are derived, never stored, so they cannot drift."""

    covered: set[str] = field(default_factory=set)
    excluded: set[str] = field(default_factory=set)
    orphans: set[str] = field(default_factory=set)
    blocks: dict[str, int] = field(default_factory=dict)

    @property
    def total(self) -> int:
        return len(self.covered) + len(self.excluded) + len(self.orphans)


def all_test_files(repo: Path) -> set[str]:
    """Every test file on disk, as CI would name it.

    The names pytest collects under tests/ at any depth: test_*.py and *_test.py.
    Looking only at tests/test_*.py let a nested or *_test.py file be added and
    never judged (Codex on GRIP#8404).
    """
    tests = repo / "tests"
    found = chain(tests.rglob("test_*.py"), tests.rglob("*_test.py"))
    return {p.relative_to(repo).as_posix() for p in found if p.is_file()}


def _code_lines(text: str) -> list[str]:
    """A workflow's lines without its comment lines.

    A line whose first non-blank character is `#` is a YAML comment, or a shell
    comment inside a run: block. A comment that names a test is not a step that
    runs it: counting comments marked two tests covered whose own comments say
    nothing runs them (tests/test_grip_worktree.py,
    tests/test_convergence_integration.py; Codex on GRIP#8404). A comment after
    code on the same line is still read.
    """
    return [line for line in text.splitlines() if not line.lstrip().startswith("#")]


def _runs_on_pull_requests(lines: list[str]) -> bool:
    """Whether a workflow's top-level `on:` names pull_request or pull_request_target.

    Read as text, from the `on:` line and the indented lines under it, so the
    bare-runner jobs that call this need no YAML parser.
    """
    for i, line in enumerate(lines):
        if _ON_KEY.match(line):
            rest = takewhile(lambda nxt: not nxt.strip() or nxt[0].isspace(), lines[i + 1:])
            return bool(_PR_TRIGGER.search("\n".join([line, *rest])))
    return False


def _workflow_text(repo: Path) -> str:
    """The code lines of every workflow that runs on pull requests.

    Only a pull-request workflow can run a test before it merges. A workflow
    triggered only by a schedule, a push to main or a manual dispatch covers
    nothing here: three tests named only in such workflows had counted as
    covered with no pull-request job running them (Codex on GRIP#8404). Two
    limits remain: a job whose own `if:` skips pull requests is still read, and
    so is a step disabled with `if: false`. No job or step that names a test has
    either today, and spotting them needs a YAML parser that the bare-runner
    jobs do not install.

    Both extensions GitHub accepts are read, .yml and .yaml; reading only .yml
    would call a test bound in a .yaml workflow an orphan (Codex on GRIP#8404).
    """
    wf = repo / ".github" / "workflows"
    if not wf.is_dir():
        raise FileNotFoundError(f"no workflows directory at {wf}")
    files = sorted(chain(wf.glob("*.yml"), wf.glob("*.yaml")))
    per_file = (_code_lines(p.read_text(encoding="utf-8", errors="replace")) for p in files)
    return "\n".join(chain.from_iterable(lines for lines in per_file
                                         if _runs_on_pull_requests(lines)))


def _named_tests(text: str, everything: set[str]) -> set[str]:
    """The test files on disk that ``text`` names, however the path is written.

    Each `.py` word is tried whole and from every `/` inside it, so
    `./tests/x.py`, `$GITHUB_WORKSPACE/tests/x.py` and `tests/x.py::case` all
    name tests/x.py. Matching the discovered paths, rather than a pattern of
    allowed characters, lets a path with a hyphen be named too (Codex on
    GRIP#8404).
    """
    named: set[str] = set()
    for word in _PY_WORD.findall(text):
        starts = [0] + [i + 1 for i, ch in enumerate(word) if ch == "/"]
        named.update(word[i:] for i in starts if word[i:] in everything)
    return named


def blocks_referenced(repo: Path) -> set[str]:
    """Block names the workflows actually invoke.

    Taken from the workflows rather than from config/test-blocks/, because a
    block file that no job runs covers nothing. Listing the directory would
    count it as coverage and overstate the picture.
    """
    return set(_BLOCK_RE.findall(_workflow_text(repo)))


def _resolve_block(repo: Path, name: str) -> set[str]:
    """Resolve one block through the real resolver CI uses.

    Raises on failure rather than returning an empty set: a block that silently
    resolved to nothing would be indistinguishable from one that legitimately
    covers nothing, and would quietly inflate the orphan count.
    """
    script = repo / "scripts" / "gate-test-block.sh"
    if not script.is_file():
        raise FileNotFoundError(f"resolver missing: {script}")
    proc = subprocess.run(
        ["bash", str(script), name],
        cwd=str(repo), capture_output=True, text=True, timeout=120,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"gate-test-block.sh {name} exited {proc.returncode}: "
            f"{proc.stderr.strip()[:300]}"
        )
    return {ln.strip() for ln in proc.stdout.splitlines() if ln.strip()}


def _excluded_entries(repo: Path) -> set[str]:
    """Files recorded as unable to run in a *-excluded.txt, each with its reason.

    The reason follows `#` on the entry's own line, as every entry carries it.
    A bare path is not an exclusion: accepting one would let a pull request clear
    the added-orphan check by listing its test with no reason (Codex on GRIP#8404).
    """
    block_dir = repo / "config" / "test-blocks"
    if not block_dir.is_dir():
        return set()
    every_line = chain.from_iterable(
        f.read_text(encoding="utf-8", errors="replace").splitlines()
        for f in sorted(block_dir.glob("*excluded*.txt"))
    )
    split = (line.partition("#") for line in every_line)
    return {path.split()[0] for path, _, reason in split if path.strip() and reason.strip()}


def classify(repo: Path) -> Coverage:
    """Split every test file on disk into covered / excluded / orphan."""
    everything = all_test_files(repo)
    named = _named_tests(_workflow_text(repo), everything)

    cov = Coverage()
    reached = set(named)
    for name in sorted(blocks_referenced(repo)):
        resolved = _resolve_block(repo, name) & everything
        cov.blocks[name] = len(resolved)
        reached |= resolved

    cov.covered = reached
    cov.excluded = (_excluded_entries(repo) & everything) - reached
    cov.orphans = everything - reached - cov.excluded
    return cov


def render(cov: Coverage) -> str:
    """A report a human reads, not a verdict."""
    pct = (100.0 * len(cov.orphans) / cov.total) if cov.total else 0.0
    head = [
        "CI test coverage",
        f"  test files on disk : {cov.total}",
        f"  covered by CI      : {len(cov.covered)}",
        f"  excluded (reasoned): {len(cov.excluded)}",
        f"  ORPHANS            : {len(cov.orphans)}  ({pct:.1f}%)",
        "  blocks resolved via scripts/gate-test-block.sh:",
    ]
    rows = [f"    {name:<34} {n}" for name, n in sorted(cov.blocks.items())]
    return "\n".join(head + rows)


def added_orphans(cov: Coverage, added: Iterable[str]) -> list[str]:
    """The test files among ``added`` that no CI route reaches, sorted.

    ``added`` is what `git diff --name-only` prints for a pull request, so it
    holds every kind of file; only test files CI would name can match. An
    excluded file carries a recorded reason, so it is not an orphan and passes.
    """
    return sorted(cov.orphans.intersection(path.strip() for path in added))


_BIND_HINT = (
    "Bind each one where a CI job runs it: list it in a config/test-blocks/ block\n"
    "such as unbound-sweep.txt, or give it a *hook*, *settings* or *registry* name\n"
    "and list it in config/test-blocks/hooks-settings-registry.txt. A test that\n"
    "cannot run on a bare runner goes in a config/test-blocks/*-excluded.txt line,\n"
    "with the reason after a # on the same line; a bare path is not an exclusion."
)


def _fail_on_added(root: Path, listing: Path) -> int:
    """Exit status for --fail-on-added: 0 clean, 1 an added orphan, 2 no list."""
    try:
        text = listing.read_text(encoding="utf-8", errors="surrogateescape")
    except OSError as exc:
        print(f"cannot read the list of added files ({exc}); refusing to report "
              "a pull request as clean without it", file=sys.stderr)
        return 2
    # `git diff -z` separates paths with NUL and nothing else, so a name may hold
    # any other character, a newline included; splitting such a list on lines
    # would break that name and let it pass (Codex on GRIP#8404). A list with no
    # NUL, from a local run, is read line by line. surrogateescape decodes a
    # non-UTF-8 name as Path.glob does on POSIX.
    parts = text.split("\0") if "\0" in text else text.splitlines()
    added = [path for path in parts if path]
    orphans = added_orphans(classify(root), added)
    if orphans:
        print("This pull request adds test files that no CI job runs:", file=sys.stderr)
        print("\n".join(f"  {o}" for o in orphans), file=sys.stderr)
        print(_BIND_HINT, file=sys.stderr)
        return 1
    print("Every test file this pull request adds is run by a CI job or excluded "
          "with a reason.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Which test files CI runs, and which it reaches by no route.")
    parser.add_argument("root", nargs="?", default=".",
                        help="repository root (default: the current directory)")
    parser.add_argument("--list", action="store_true", help="also name every orphan")
    parser.add_argument(
        "--fail-on-added", metavar="FILE", type=Path,
        help="exit 1 if a test file listed in FILE (the files a pull request "
             "adds, one per line) is an orphan; prints no report")
    args = parser.parse_args(argv)
    root = Path(args.root).resolve()
    if args.fail_on_added is not None:
        return _fail_on_added(root, args.fail_on_added)
    result = classify(root)
    print(render(result))
    if args.list:
        print("\norphans:")
        print("\n".join(f"  {o}" for o in sorted(result.orphans)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
