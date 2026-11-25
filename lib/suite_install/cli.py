"""The suite installer's command line: run every step and say what each one found.

Run it from the root of a GRIP checkout, which is also the copy it installs GRIP from:

    python3 -m lib.suite_install [--dry-run]

A run that stops part way can simply be started again, because steps that already hold
are skipped. The exit codes are in EXIT_HELP below.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Callable, Optional, Sequence, TextIO

from . import products, steps

SOURCE = Path(__file__).resolve().parents[2]
EXIT_DONE, EXIT_FAILED, EXIT_WOULD_INSTALL, EXIT_STOPPED = 0, 1, 2, 130
EXIT_HELP = ("exit codes: 0 every step holds, checked again at the end; 1 a step failed, "
             "or a later step broke an earlier one; 2 a dry run found steps it would "
             "install; 130 stopped with Ctrl-C")
HEADER = "GRIP Suite: installing GRIP, HAL, HAPPI and GRASP, and proving each one answers."
DRY_HEADER = "GRIP Suite dry run: every step is checked, and nothing is installed."
CARRY_ON = "run the same command again; steps that already hold are skipped."
INDENT = "      "


def parser() -> argparse.ArgumentParser:
    found = argparse.ArgumentParser(
        prog="python3 -m lib.suite_install",
        description="Install GRIP, HAL, HAPPI and GRASP, and prove that each one works.",
        epilog=EXIT_HELP)
    found.add_argument("--dry-run", action="store_true",
                       help="check every step and install nothing")
    return found


def default_steps() -> list[steps.Step]:
    return products.suite_steps(products.Layout(home=Path.home(), source=SOURCE))


class Progress:
    """Prints a numbered line as each step starts, then what the step found."""

    def __init__(self, out: TextIO, total: int):
        self.out, self.total, self.count = out, total, 0

    def __call__(self, step: steps.Step, result: Optional[steps.StepResult]) -> None:
        if result is None or result.status == steps.NOT_REACHED:
            self.count += 1
            self._line(f"[{self.count}/{self.total}] {step.name}")
        if result is not None:
            self._line(INDENT + _said(result).replace("\n", "\n" + INDENT + "  "))

    def _line(self, text: str) -> None:
        print(text, file=self.out, flush=True)


def _said(result: steps.StepResult) -> str:
    timed = result.status in (steps.DONE, steps.FAILED)
    timing = f" in {result.seconds:.0f} s" if timed else ""
    return f"{result.status}{timing}: {result.detail}"


def _one_line(text: str) -> str:
    """A reason may carry a second line; the closing line keeps it on one."""
    return "; ".join(text.splitlines())


def _why_not(report: steps.Report) -> str:
    failed = [r.name for r in report.results if r.status == steps.FAILED]
    if failed:
        return f"Not complete: {failed[0]} failed. Fix the reason above, then {CARRY_ON}"
    broken = [f"{r.name} ({_one_line(r.detail)})" for r in report.rechecked
              if r.status == steps.BROKEN_LATER]
    if broken:
        return ("Not complete: every step passed in turn, but at the end a later step had "
                f"broken an earlier one, which no longer held: {', '.join(broken)}")
    return "Not complete: no step ran."


def verdict(report: steps.Report, dry_run: bool) -> tuple[str, int]:
    """The closing line and the exit code. report.complete decides success."""
    if report.complete:
        if dry_run:
            return "Dry run: every step already holds, so nothing would be installed.", EXIT_DONE
        return (f"Complete: all {len(report.results)} steps hold, checked again at the end.",
                EXIT_DONE)
    waiting = [r.name for r in report.results if r.status == steps.WOULD_INSTALL]
    failed = any(r.status == steps.FAILED for r in report.results)
    if dry_run and waiting and not failed:
        return (f"Dry run, nothing installed: {len(waiting)} of {len(report.results)} steps "
                f"would install ({', '.join(waiting)}).", EXIT_WOULD_INSTALL)
    return _why_not(report), EXIT_FAILED


def main(argv: Optional[Sequence[str]] = None,
         make_steps: Callable[[], list[steps.Step]] = default_steps,
         out: Optional[TextIO] = None) -> int:
    out = out or sys.stdout
    args = parser().parse_args(argv)
    plan = make_steps()
    print(DRY_HEADER if args.dry_run else HEADER, file=out, flush=True)
    try:
        report = steps.run(plan, dry_run=args.dry_run, progress=Progress(out, len(plan)))
    except KeyboardInterrupt:
        print(f"\nStopped. To carry on, {CARRY_ON}", file=out)
        return EXIT_STOPPED
    message, code = verdict(report, args.dry_run)
    print(f"\n{message}", file=out)
    return code
