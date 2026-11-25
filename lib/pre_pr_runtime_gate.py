"""pre_pr_runtime_gate — close the write→runtime "buggy_code" gap (ADVISORY).

Why this exists (do not re-derive — grounded in tonight's /insights):
    `buggy_code` is the #1 friction class — 39.3% of sessions — and the
    canonical pattern is "bugs surface only during live testing", i.e. AFTER
    the code is written but BEFORE (or instead of) anyone runs it. GRIP
    already has two adjacent guards that do NOT close this gap:

      - Rule 18 (JIT correct-by-construction) is *write-time* — it shapes the
        code as it is authored, but cannot observe runtime behaviour.
      - Rule 14 (Goodhart guard) is *test-design-time* — it asserts a test
        CAN fail, but does not assert the changed code was actually RUN green.

    The gap is between code WRITTEN and code EXERCISED. This gate converts
    "a test file exists for the changed module" into "the changed module's
    test was actually executed and passed" — the runtime-exercise signal,
    checked just before a PR opens.

SAFETY — ADVISORY BY DEFAULT (non-negotiable):
    ~7 concurrent autonomous sessions open PRs continuously. A hard
    PR-blocking gate would break them. So this ships WARN-ONLY:

      * `check` with no flags ALWAYS exits 0 (advisory). It prints the
        verdict; it never blocks anything.
      * There is NO PreToolUse hook here and NO `.github/workflows` change.
        The only consumers are documented one-line advisory calls in
        /create-pr and /auto-ship that print the warning and proceed.
      * Hard enforcement is a documented, deferred OPT-IN (`--enforce`),
        validated by the registered hypothesis before it is ever made a
        default. `--enforce` exits 1 ONLY when status == "block".

    Upgrade path to enforcement (deferred until the hypothesis confirms):
      1. Validate `buggy_code` prevalence drop + flag rate over 30 RSI PRs.
      2. If confirmed, add an OPT-IN wrapper that runs
         `pre_pr_runtime_gate check --enforce` and treats a non-zero exit as
         a soft fail surfaced to the operator (still never a silent block of
         concurrent autonomous sessions — opt-in per workflow).
      3. Only after that, consider a PreToolUse gate (separate PR, its own
         falsifier). NEVER widen authority and use it in the same change.

Scope (Broly-scoped): the gate only fires for `lib/*.py` changes made inside
an RSI / sprint / autonomous-loop context — that is where unexercised
changed code is most likely to regress and where the loop has no human at
the console to catch it live. Outside that context it returns "skip".

Design:
    Pure logic + a thin CLI. Every collaborator that touches git or
    subprocess is dependency-injected so the unit tests need no real git,
    no real pytest run, and no real yolo marker. Each function stays under
    cyclomatic-complexity 15 (rules/cc-enforcement.md).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))
from claude_dir import get_claude_dir  # noqa: E402

CLAUDE_DIR = get_claude_dir()

# ---------------------------------------------------------------------------
# Injected boundaries (DIP — tests pass fakes; production gets real ones)
# ---------------------------------------------------------------------------

# Run a git command (list argv) → captured stdout text. Default = real git.
GitRunner = Callable[[list[str]], str]
# Run a single test file → True if it ran green. Default = bin/grip-test.
TestRunner = Callable[[str], bool]
# Decide whether we are in an RSI/sprint context. Default = real env/marker.
ContextProbe = Callable[[], bool]


def _real_git(argv: list[str]) -> str:
    """Run a git command from the CLAUDE_DIR repo, return stdout (empty on err)."""
    try:
        out = subprocess.run(
            ["git", *argv],
            cwd=str(CLAUDE_DIR),
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return out.stdout if out.returncode == 0 else ""


def _real_test_runner(test_file: str) -> bool:
    """Run one test file via bin/grip-test; True iff exit code 0 (green)."""
    runner = CLAUDE_DIR / "bin" / "grip-test"
    try:
        result = subprocess.run(
            [str(runner), test_file],
            cwd=str(CLAUDE_DIR),
            capture_output=True,
            text=True,
            timeout=600,
        )
    except (OSError, subprocess.SubprocessError):
        # Fail-safe for ADVISORY: if we cannot run it, we cannot claim green.
        return False
    return result.returncode == 0


# ---------------------------------------------------------------------------
# Pure logic
# ---------------------------------------------------------------------------


def changed_lib_files(base: str = "origin/main", *, git: GitRunner = _real_git) -> list[str]:
    """Return lib/*.py paths changed vs ``base``...HEAD.

    Excludes test files (``tests/`` and ``test_*.py``) and ``__pycache__`` —
    a changed test is not "newly-changed production code that needs running".
    """
    raw = git(["diff", "--name-only", f"{base}...HEAD"])
    files = [line.strip() for line in raw.splitlines() if line.strip()]
    out: list[str] = []
    for path in files:
        if not path.startswith("lib/") or not path.endswith(".py"):
            continue
        if "/tests/" in path or path.startswith("tests/"):
            continue
        if "__pycache__" in path or Path(path).name.startswith("test_"):
            continue
        out.append(path)
    return out


def _test_candidates(lib_file: str) -> list[str]:
    """Return the realistic GRIP test-path candidates for a changed lib file.

    A subdir lib file can be tested under several real GRIP conventions, so a
    stem-only guess (``lib/gates/foo.py`` → ``tests/test_foo.py``) misses
    tests organised by subdir or by underscored relative path. Candidates,
    in preference order:

      1. ``tests/test_<stem>.py``                  (flat — GRIP's common case)
      2. ``tests/<subdirs>/test_<stem>.py``        (mirrored subdir layout)
      3. ``tests/test_<underscored_relpath>.py``   (lib/gates/foo.py →
         tests/test_gates_foo.py — collision-avoiding flat naming)

    For a top-level ``lib/foo.py`` candidates 1 and 3 coincide and 2 reduces
    to candidate 1, so the list de-duplicates to a single path.
    """
    rel = Path(lib_file)
    stem = rel.stem
    # Path parts after the leading "lib/" component, excluding the filename.
    subdirs = list(rel.parts[1:-1])  # e.g. lib/gates/foo.py → ["gates"]
    candidates = [f"tests/test_{stem}.py"]
    if subdirs:
        candidates.append("tests/" + "/".join(subdirs) + f"/test_{stem}.py")
        candidates.append("tests/test_" + "_".join([*subdirs, stem]) + ".py")
    # Preserve order, drop duplicates (top-level files collapse to one).
    seen: set[str] = set()
    ordered: list[str] = []
    for c in candidates:
        if c not in seen:
            seen.add(c)
            ordered.append(c)
    return ordered


def locate_test(lib_file: str, *, root: Path = CLAUDE_DIR) -> Optional[str]:
    """Return the first existing realistic test path for ``lib_file``, else None.

    Searches the GRIP test-path conventions in ``_test_candidates`` order and
    returns the first that exists on disk under ``root``. ``lib/gates/foo.py``
    resolves to ``tests/test_gates_foo.py`` (or a mirrored
    ``tests/gates/test_foo.py``) when a bare ``tests/test_foo.py`` is absent.
    """
    for candidate in _test_candidates(lib_file):
        if (root / candidate).exists():
            return candidate
    return None


def _branch_is_loop(branch: str) -> bool:
    """True if branch name signals an autonomous loop (feat/rsi|sprint|auto)."""
    return any(
        branch.startswith(prefix)
        for prefix in ("feat/rsi", "feat/sprint", "feat/auto")
    )


def _yolo_marker_present(root: Path = CLAUDE_DIR) -> bool:
    """True if the yolo-mode marker file exists (presence = loop active).

    Presence alone is the signal — the canonical TTL/validity check lives in
    lib/gates/yolo_mode.py; we only need "is an autonomous loop running".
    """
    marker = root / "state" / "session" / "yolo-mode.json"
    if not marker.exists():
        return False
    try:
        json.loads(marker.read_text())
    except (OSError, ValueError):
        return False
    return True


def in_rsi_sprint_context(
    *,
    env: Optional[dict] = None,
    git: GitRunner = _real_git,
    root: Path = CLAUDE_DIR,
) -> bool:
    """True if we are in an RSI / sprint / autonomous-loop context.

    Three independent signals (any one suffices):
      1. yolo marker present (``state/session/yolo-mode.json``)
      2. env GRIP_RSI_ACTIVE / GRIP_ULTRADO_ACTIVE set (truthy)
      3. current branch matches feat/(rsi|sprint|auto)
    """
    env = os.environ if env is None else env
    if _yolo_marker_present(root):
        return True
    if env.get("GRIP_RSI_ACTIVE") or env.get("GRIP_ULTRADO_ACTIVE"):
        return True
    branch = git(["rev-parse", "--abbrev-ref", "HEAD"]).strip()
    return _branch_is_loop(branch)


@dataclass
class ExerciseResults:
    """Outcome of attempting to exercise each changed lib file."""

    ran_green: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    unexercised: list[str] = field(default_factory=list)  # changed, no test


def exercise(
    changed: list[str],
    *,
    test_runner: TestRunner = _real_test_runner,
    root: Path = CLAUDE_DIR,
) -> ExerciseResults:
    """Run each changed lib file's test (if any); record the outcome.

    A file with NO test is "unexercised" — we cannot assert it was run green.
    """
    results = ExerciseResults()
    for lib_file in changed:
        test_file = locate_test(lib_file, root=root)
        if test_file is None:
            results.unexercised.append(lib_file)
            continue
        if test_runner(test_file):
            results.ran_green.append(lib_file)
        else:
            results.failed.append(lib_file)
    return results


def skip_reason(changed_count: int, in_context: bool) -> Optional[str]:
    """Return the distinct skip reason, or None if the gate should NOT skip.

    The two skip causes are genuinely different and must not be conflated:
      - not in an RSI/sprint context (gate is out of scope by design)
      - in context, but no ``lib/*.py`` files changed (nothing to exercise)

    Context is checked FIRST so the reason is computed from the *true* cause —
    the caller zeroes ``changed`` to ``[]`` when out of context, which would
    otherwise mislabel an out-of-context run as "no lib/*.py changes".
    """
    if not in_context:
        return "not an RSI/sprint context"
    if changed_count == 0:
        return "no lib/*.py changes"
    return None


def verdict(
    changed: list[str],
    results: ExerciseResults,
    *,
    enforce: bool = False,
    in_context: bool = True,
    skip_reason_text: Optional[str] = None,
) -> dict:
    """Compute the gate verdict.

    status:
      - "skip"  : no lib changes OR not in RSI/sprint context
      - "pass"  : every changed lib file has a test that ran green
      - "warn"  : some unexercised/failed AND not enforce (advisory)
      - "block" : some unexercised/failed AND enforce (opt-in hard mode)

    ``skip_reason_text`` lets the caller supply the disambiguated reason
    computed BEFORE it zeroed ``changed`` (see ``skip_reason``). When omitted,
    the reason is derived locally — but note a locally-derived reason cannot
    tell "out of context" from "no changes" once the caller has zeroed
    ``changed``, so callers that zero ``changed`` MUST pass it explicitly.
    """
    if not changed or not in_context:
        reason = skip_reason_text or skip_reason(len(changed), in_context) or (
            "no lib/*.py changes"
        )
        return {
            "status": "skip",
            "unexercised": [],
            "failed": [],
            "reasons": [reason],
        }
    problems = results.unexercised + results.failed
    if not problems:
        return {
            "status": "pass",
            "unexercised": [],
            "failed": [],
            "reasons": [f"all {len(changed)} changed lib file(s) ran green"],
        }
    reasons: list[str] = []
    if results.unexercised:
        reasons.append(
            f"{len(results.unexercised)} changed lib file(s) have NO test "
            f"(unexercised): {', '.join(results.unexercised)}"
        )
    if results.failed:
        reasons.append(
            f"{len(results.failed)} changed lib file(s) FAILED their test: "
            f"{', '.join(results.failed)}"
        )
    return {
        "status": "block" if enforce else "warn",
        "unexercised": list(results.unexercised),
        "failed": list(results.failed),
        "reasons": reasons,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _run_check(base: str, enforce: bool) -> dict:
    """Wire the real boundaries together and produce a verdict dict.

    The skip reason is computed from the TRUE cause before ``changed`` is
    zeroed for the out-of-context path, so "not an RSI/sprint context" is
    never mislabelled as "no lib/*.py changes" (concern #3).
    """
    in_context = in_rsi_sprint_context()
    # Only diff when in context; but capture the count for the reason first.
    changed = changed_lib_files(base) if in_context else []
    reason = skip_reason(len(changed), in_context)
    results = exercise(changed) if changed else ExerciseResults()
    return verdict(
        changed,
        results,
        enforce=enforce,
        in_context=in_context,
        skip_reason_text=reason,
    )


def main(argv: Optional[list[str]] = None) -> int:
    """CLI entry. DEFAULT (no --enforce) ALWAYS exits 0 (advisory).

    Usage:
        python lib/pre_pr_runtime_gate.py check --base origin/main [--enforce]
    """
    args = sys.argv[1:] if argv is None else argv
    if not args or args[0] != "check":
        sys.stderr.write(
            "usage: pre_pr_runtime_gate.py check "
            "[--base <ref>] [--enforce]\n"
        )
        return 0  # advisory tool: never hard-fail on bad invocation
    base = "origin/main"
    enforce = False
    i = 1
    while i < len(args):
        if args[i] == "--base" and i + 1 < len(args):
            base = args[i + 1]
            i += 2
            continue
        if args[i] == "--enforce":
            enforce = True
        i += 1
    result = _run_check(base, enforce)
    sys.stdout.write(json.dumps(result, indent=2) + "\n")
    # Advisory default: exit 0 unless (enforce AND block).
    if enforce and result["status"] == "block":
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
