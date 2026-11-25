"""The suite installer's step engine: a step counts only when its proof passes.

A step is a name, a proof function and an optional install function. The engine checks the
proof first, so a step that is already true is never installed again and a rerun is safe.
It installs only when the proof fails, and counts the step only if the proof passes
afterwards; what the installer itself returned is never the evidence. The first failed step
stops the run, because later steps build on earlier ones. When every step has passed, every
proof runs once more against the end state: a later step can break an earlier one (two
installers sharing one venv), and that must not pass as complete.

A dry run checks every proof and installs nothing.

A proof must only read the system. The engine cannot enforce that: it is the contract
every proof function signs, and a dry run is only as harmless as its proofs.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, Iterable, Optional

ALREADY_DONE = "already done"
DONE = "done"
FAILED = "failed"
NOT_REACHED = "not reached"
WOULD_INSTALL = "would install"
STILL_TRUE = "still true"
BROKEN_LATER = "broken by a later step"


@dataclass(frozen=True)
class Proof:
    """What a check found: whether it holds, and in plain words what was seen."""

    ok: bool
    detail: str


@dataclass(frozen=True)
class Step:
    """One thing the suite needs, how to prove it, and how to make it true."""

    name: str
    prove: Callable[[], Proof]
    install: Optional[Callable[[], None]] = None


@dataclass(frozen=True)
class StepResult:
    name: str
    status: str
    detail: str
    seconds: float


@dataclass(frozen=True)
class Report:
    """Every step's result, the end-state recheck, and whether the whole run holds."""

    results: tuple[StepResult, ...]
    rechecked: tuple[StepResult, ...]
    complete: bool


def check(step: Step) -> Proof:
    """Run a step's proof. A proof that raises is a proof that does not hold."""
    try:
        return step.prove()
    except Exception as exc:  # noqa: BLE001 -- any failure to check is a failed check
        return Proof(False, f"the check itself failed: {type(exc).__name__}: {exc}")


def _install(step: Step) -> Optional[str]:
    """Run a step's installer and return why it failed, or None."""
    if step.install is None:
        return "nothing installs this; it must already be true"
    try:
        step.install()
    except Exception as exc:  # noqa: BLE001 -- reported, then the proof decides
        return f"install failed: {type(exc).__name__}: {exc}"
    return None


def _result(step: Step, status: str, detail: str, started: float) -> StepResult:
    return StepResult(step.name, status, detail, time.monotonic() - started)


def run_step(step: Step, dry_run: bool = False) -> StepResult:
    """Prove, install only if needed, and prove again."""
    started = time.monotonic()
    proof = check(step)
    if proof.ok:
        return _result(step, ALREADY_DONE, proof.detail, started)
    if dry_run and step.install is not None:
        return _result(step, WOULD_INSTALL, proof.detail, started)
    # A check-only step has nothing to run here, in a dry run or a real one, so it
    # fails with its proof's reason instead of claiming a run could fix it.
    error = _install(step)
    proof = check(step)
    if proof.ok:
        return _result(step, DONE, proof.detail, started)
    detail = f"{error}; {proof.detail}" if error else proof.detail
    return _result(step, FAILED, detail, started)


def recheck(steps: tuple) -> tuple:
    """Prove every step again against the end state."""
    results = []
    for step in steps:
        started = time.monotonic()
        proof = check(step)
        status = STILL_TRUE if proof.ok else BROKEN_LATER
        results.append(_result(step, status, proof.detail, started))
    return tuple(results)


Progress = Callable[[Step, Optional[StepResult]], None]


def _tell(progress: Optional[Progress], step: Step, result: Optional[StepResult] = None) -> None:
    if progress is not None:
        progress(step, result)


def run(steps: Iterable[Step], dry_run: bool = False,
        progress: Optional[Progress] = None) -> Report:
    """Run the steps in order and report; see the module docstring for the rules.

    progress, when given, hears each step as it starts (with no result) and as it ends;
    a step not reached is heard once, with its result."""
    steps = tuple(steps)
    results = []
    stopped = False
    for step in steps:
        if stopped:
            result = StepResult(step.name, NOT_REACHED, "an earlier step failed", 0.0)
        else:
            _tell(progress, step)
            result = run_step(step, dry_run)
            stopped = result.status == FAILED
        results.append(result)
        _tell(progress, step, result)
    passed = bool(results) and all(r.status in (ALREADY_DONE, DONE) for r in results)
    if dry_run or not passed:
        return Report(tuple(results), (), passed)
    rechecked = recheck(steps)
    return Report(tuple(results), rechecked, all(r.status == STILL_TRUE for r in rechecked))
