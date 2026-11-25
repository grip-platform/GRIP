"""mob_guards -- safety guards for the GRIP wrappers over mob.sh (start/next/done).

mob.sh itself (https://mob.sh) is a thin layer over Git that moves a
work-in-progress branch between typists. These wrappers add guards on top of
it, requested explicitly for this task rather than taken from the Guild page
(the page names no guards of its own):

  * clean tree    -- mob-start only. Mirrors mob's own refusal, with a
                      friendlier message. NOT applied to next/done: those
                      commands exist precisely to commit/collapse whatever
                      is dirty, so refusing a dirty tree there would break
                      the tool's entire purpose.
  * not main/master -- mob-next and mob-done only (V>> decision, GRIP#9266
                      follow-up: mob-start ALLOWS main/master, matching
                      upstream mob.sh's own documented happy path, which
                      starts "mob start" from main). next/done still refuse,
                      so a session is never handed over or finished directly
                      against a protected branch.
  * mob on PATH   -- all three commands. A precondition, not a page item:
                      there is nothing to guard if the handover tool is not
                      installed.
  * tests pass    -- mob-next only, exactly as specified. Runs the repo's
                      own `npm test` before allowing a handover; a repo with
                      no configured test script is not blocked (fails open
                      on missing configuration, fails closed on an actual
                      red suite, and fails closed -- not open -- on a
                      package.json that exists but cannot even be parsed,
                      which is not "no test script configured").

Two kinds of function, so tests can fake the impure half only:
  * guard_* functions are pure: given already-collected state, decide ok/not.
  * collect_* and run_tests touch the real machine (git, the filesystem,
    PATH); tests fake these, never the guard_* decision logic.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, TextIO

PROTECTED_BRANCHES = {"main", "master"}


class PackageJsonUnreadable(Exception):
    """package.json exists but could not be parsed.

    Distinct from "no package.json" / "no scripts.test" -- those mean
    nothing to test, which is fine. A package.json that exists but is
    broken means `npm test` itself would almost certainly fail too, so
    `preflight_next` must not treat it as "no test script configured"
    (Codex, GRIP#9266 thread 3).
    """


@dataclass
class GuardResult:
    ok: bool
    reason: str = ""
    fix: str = ""


# ---------------------------------------------------------------------------
# Pure guards -- take already-collected state, never touch the machine.
# ---------------------------------------------------------------------------

def guard_clean_tree(porcelain_output: str) -> GuardResult:
    if porcelain_output.strip():
        return GuardResult(
            False, "the working tree has uncommitted changes",
            "commit or stash them, or pass --include-uncommitted-changes to "
            "mob yourself if you truly want them carried in")
    return GuardResult(True)


def guard_not_protected_branch(branch_name: str) -> GuardResult:
    if branch_name.strip() in PROTECTED_BRANCHES:
        return GuardResult(
            False, f"currently on '{branch_name}', a protected branch",
            # NOT "mob/<topic>": "mob/" is mob's own default WIP branch
            # prefix (confirmed empirically -- `mob config` reports
            # MOB_WIP_BRANCH_PREFIX="mob/" with no .mob file present at
            # all). A branch literally named "mob/<topic>" is parsed by
            # mob itself as an EXISTING WIP branch, and `mob start` then
            # fails trying to derive a base branch that does not exist
            # (Codex, GRIP#9266 thread 8; reproduced directly: checking
            # out "mob/topic-y" and running `mob start` fails with
            # "Remote branch origin/topic is missing").
            "git checkout -b <topic> first (an ordinary branch name, not "
            "starting with mob/ -- that prefix is mob's own WIP namespace)")
    return GuardResult(True)


def guard_mob_available(mob_path: Optional[str]) -> GuardResult:
    if not mob_path:
        return GuardResult(False, "mob is not on PATH",
                            "brew install remotemobprogramming/brew/mob")
    return GuardResult(True)


def guard_tests_pass(test_command: Optional[list[str]],
                      run_result: Optional[subprocess.CompletedProcess]) -> GuardResult:
    if test_command is None:
        return GuardResult(True, "no test script in package.json; skipping")
    if run_result is None or run_result.returncode != 0:
        tail = "" if run_result is None else ((run_result.stdout or "") +
                                                (run_result.stderr or ""))[-500:]
        return GuardResult(False, f"repo tests failed: {tail}".strip(),
                            "fix the failing tests before handing over")
    return GuardResult(True)


# ---------------------------------------------------------------------------
# Collectors -- the only functions that touch the real machine. Tests fake
# these directly, never the guard_* decision logic above.
# ---------------------------------------------------------------------------

def collect_porcelain(cwd: Path) -> str:
    result = subprocess.run(["git", "status", "--porcelain"], cwd=cwd,
                             capture_output=True, text=True, timeout=30)
    return result.stdout


def collect_branch(cwd: Path) -> str:
    result = subprocess.run(["git", "branch", "--show-current"], cwd=cwd,
                             capture_output=True, text=True, timeout=30)
    return result.stdout.strip()


def collect_mob_path() -> Optional[str]:
    return shutil.which("mob")


def collect_test_command(cwd: Path) -> Optional[list[str]]:
    """None means "nothing to test" (no package.json, or no scripts.test).

    Raises PackageJsonUnreadable when package.json EXISTS but cannot be
    parsed -- that is not the same situation, and must not be folded into
    the same "skip" outcome (Codex, GRIP#9266 thread 3).
    """
    pkg = cwd / "package.json"
    if not pkg.exists():
        return None
    try:
        data = json.loads(pkg.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PackageJsonUnreadable(
            f"package.json exists but could not be parsed: {exc}") from exc
    if data.get("scripts", {}).get("test"):
        return ["npm", "test"]
    return None


def run_tests(cwd: Path, test_command: list[str]) -> subprocess.CompletedProcess:
    """Run the repo's test command; a hang becomes a clean failure, not a crash.

    subprocess.TimeoutExpired is NOT an OSError subclass, so an uncaught hang
    here would crash bin/grip-mob-next with a traceback instead of the clean
    "fix the failing tests" refusal every other red-suite case already gets
    (AGORA council, GRIP#9266 -- the same class of bug already fixed for
    mob_doctor.py's _probe, found again here in mob_guards.py's own
    subprocess call).
    """
    try:
        return subprocess.run(test_command, cwd=cwd, capture_output=True,
                               text=True, timeout=600)
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout if isinstance(exc.stdout, str) else ""
        return subprocess.CompletedProcess(
            args=test_command, returncode=124, stdout=stdout,
            stderr=f"tests did not finish within {exc.timeout}s -- treated as a failure")


# ---------------------------------------------------------------------------
# Orchestration -- one function per wrapper command, composing the guards
# above with the real collectors. A bin/ wrapper calls exactly one of these.
# ---------------------------------------------------------------------------

def preflight_start(cwd: Path) -> list[GuardResult]:
    # No guard_not_protected_branch here, deliberately: mob-start ALLOWS
    # main/master, matching upstream mob.sh's own documented flow (V>>
    # decision, GRIP#9266 follow-up).
    return [
        guard_mob_available(collect_mob_path()),
        guard_clean_tree(collect_porcelain(cwd)),
    ]


def preflight_next(cwd: Path) -> list[GuardResult]:
    # Cheap guards FIRST, and short-circuit before touching the test suite at
    # all: the test run can take minutes and write coverage/snapshot files,
    # so running it before checking whether mob is even installed or the
    # branch is protected means a refused handover still mutated the
    # repository first (Codex, GRIP#9266 round 2 thread 5).
    mob_guard = guard_mob_available(collect_mob_path())
    branch_guard = guard_not_protected_branch(collect_branch(cwd))
    if not (mob_guard.ok and branch_guard.ok):
        return [mob_guard, branch_guard]
    try:
        test_command = collect_test_command(cwd)
    except PackageJsonUnreadable as exc:
        return [mob_guard, branch_guard,
                GuardResult(False, str(exc), "fix package.json before handing over")]
    run_result = run_tests(cwd, test_command) if test_command else None
    return [mob_guard, branch_guard, guard_tests_pass(test_command, run_result)]


def preflight_done(cwd: Path) -> list[GuardResult]:
    return [
        guard_mob_available(collect_mob_path()),
        guard_not_protected_branch(collect_branch(cwd)),
    ]


# ---------------------------------------------------------------------------
# Dispatch -- shared by the three bin/ wrappers. _exec_mob is the one exec
# seam, so a test can fake it and assert the argv it would have run, without
# actually replacing the test process.
# ---------------------------------------------------------------------------

def _exec_mob(args: list[str]) -> None:
    os.execvp("mob", ["mob", *args])  # pragma: no cover -- replaces the process


def run_guarded(label: str, results: list[GuardResult], mob_args: list[str],
                 out: TextIO = sys.stderr) -> int:
    failures = [r for r in results if not r.ok]
    if failures:
        for r in failures:
            print(f"[grip-mob-{label}] refused: {r.reason}", file=out)
            if r.fix:
                print(f"    fix: {r.fix}", file=out)
        return 1
    _exec_mob(mob_args)
    return 0  # unreachable once _exec_mob succeeds; kept for callers that fake it
