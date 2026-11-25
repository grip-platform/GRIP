"""The suite's four products as steps: GRIP, HAL, HAPPI and GRASP (GRIP Suite, W2).

Each product is data: the commands that install it, and the commands whose answers
prove it. A proof runs the installed product's own entry point, so a step counts only
when the thing answers, never because an installer exited 0.

Why the proofs are what they are (measured 2026-11-24):

- Neither `hal` nor `happi` has a version flag. `hal --help` prints its usage before it
  loads any provider, and `happi run` answers a version request with events that end
  "completed".
- GRIP's own installer runs unattended from the checkout the suite runs from, and picks
  its fresh, merge or upgrade path itself. GRIP is proved by its venv: the core packages
  import, and the interpreter can dual-sign records (`pq_reexec.can_dual_sign`, GRIP's
  own definition of that).
- GRASP is pinned to its v0.2.0 release and installed into GRIP's venv.

Every command runs with its input closed, so a prompt reads end-of-file instead of
waiting, and its output is read as UTF-8 whatever the locale says.
"""
from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from .computer import check_computer
from .steps import Proof, Step

HAL_URL = "https://github.com/grip-platform/HAL"
GRASP_SPEC = "grasp-provenance[pqc] @ git+https://github.com/CodeTonight-SA/grasp@v0.2.0"
VERSION_REQUEST = '{"v":"happi/1.0","id":"suite-check","cmd":"version"}\n'
DUAL_SIGN = ("import sys; sys.path.insert(0, sys.argv[1]); import pq_reexec; "
             "sys.exit(0 if pq_reexec.can_dual_sign() else 1)")
TAIL_LINES = 12
# The codes run_command gives when no program answered: the shell's code for a command
# that cannot start, and GNU timeout's code for one that ran out of time (GRIP#9502).
EXIT_COULD_NOT_START = 127
EXIT_TIMED_OUT = 124


@dataclass(frozen=True)
class Layout:
    """Where the suite puts things: under one home, from one GRIP checkout."""

    home: Path
    source: Path

    @property
    def claude(self) -> Path:
        return self.home / ".claude"

    @property
    def hal(self) -> Path:
        return self.home / ".hal"

    @property
    def local_bin(self) -> Path:
        return self.home / ".local" / "bin"

    @property
    def venv_python(self) -> Path:
        """GRIP's venv interpreter, in the layout `lib/_compat.grip_python` reads."""
        if sys.platform == "win32":
            return self.claude / "venv" / "Scripts" / "python.exe"
        return self.claude / "venv" / "bin" / "python"


@dataclass(frozen=True)
class Command:
    """One command, in plain words (`label`) and as it runs."""

    label: str
    argv: tuple[str, ...]
    expect: tuple[str, ...] = ()
    stdin: str = ""
    env: tuple[tuple[str, str], ...] = ()
    cwd: Optional[Path] = None
    timeout: int = 600


@dataclass(frozen=True)
class Result:
    code: int
    out: str
    # True only when run_command stopped the program at its time limit. A program can
    # exit 124 itself, so the code alone cannot say (Codex on GRIP#9567).
    timed_out: bool = False


Runner = Callable[[Command], Result]


@dataclass(frozen=True)
class Product:
    name: str
    install: tuple[Command, ...]
    proofs: tuple[Command, ...]


class InstallFailed(RuntimeError):
    """An install command exited non-zero; the message names it and its last lines."""


def run_command(command: Command) -> Result:
    """Run one command with input closed. A command that cannot start is exit 127, and
    one that runs past its time limit is exit 124. Either way the output says why in
    plain words, because a dry run on a fresh machine shows it to a beginner."""
    env = {**os.environ, **dict(command.env)}
    try:
        done = subprocess.run(list(command.argv), input=command.stdin, capture_output=True,
                              encoding="utf-8", errors="replace", cwd=command.cwd, env=env,
                              timeout=command.timeout)
    except subprocess.TimeoutExpired as exc:
        return Result(EXIT_TIMED_OUT, _ran_out_of_time(exc, command), timed_out=True)
    except OSError as exc:
        return Result(EXIT_COULD_NOT_START, _could_not_start(exc, command))
    return Result(done.returncode, done.stdout + done.stderr)


def _ran_out_of_time(exc: subprocess.TimeoutExpired, command: Command) -> str:
    """The time limit, then what the program printed before it was stopped, which shows
    where it hung. Python can keep that partial output as bytes even in text mode."""
    printed = [part.decode("utf-8", "replace") if isinstance(part, bytes) else part
               for part in (exc.stdout, exc.stderr) if part]
    return "\n".join([f"it did not finish within {command.timeout} s", *printed])


def _could_not_start(exc: OSError, command: Command) -> str:
    """Names the missing path only when it is really absent. A script whose interpreter
    is missing raises the same error with its own path (Codex on GRIP#9567), and
    Windows names no path, so there the system's own words are kept."""
    named = exc.filename if isinstance(exc, FileNotFoundError) else None
    if named and not os.path.lexists(named):
        return f"nothing is at {named}"
    if named:
        return f"{named} is there, but could not start; a program it needs may be missing"
    return f"{command.argv[0]} did not start: {exc.strerror or exc}"


def _tail(text: str) -> str:
    return " | ".join(text.strip().splitlines()[-TAIL_LINES:]) or "(no output)"


def _answers(command: Command, result: Result) -> bool:
    return result.code == 0 and all(text in result.out for text in command.expect)


def _not_shown(result: Result) -> str:
    """Why a check showed nothing. "Could not run", never "not installed": a program can
    be there while its interpreter is not, and then it exits 127 itself (GRIP#9502)."""
    if result.timed_out:
        return "not shown, because the check did not finish in time"
    if result.code == EXIT_COULD_NOT_START:
        return "not shown, because the check could not run"
    return f"not shown (exit {result.code})"


def prove(product: Product, runner: Runner) -> Proof:
    """Every proof must answer; the first that does not is the reason, with its
    program's last lines on a second line."""
    for command in product.proofs:
        result = runner(command)
        if not _answers(command, result):
            return Proof(False, f"{product.name}: {command.label} -- {_not_shown(result)}\n"
                                f"{_tail(result.out)}")
    return Proof(True, f"{product.name}: " + "; ".join(c.label for c in product.proofs))


def install(product: Product, runner: Runner) -> None:
    for command in product.install:
        result = runner(command)
        if result.code != 0:
            raise InstallFailed(f"{command.label} exited {result.code}: {_tail(result.out)}")


def as_step(product: Product, runner: Runner = run_command) -> Step:
    return Step(product.name, lambda: prove(product, runner), lambda: install(product, runner))


def grip(layout: Layout) -> Product:
    python = str(layout.venv_python)
    installer = ("bash", str(layout.source / "scripts" / "install.sh"), "--non-interactive")
    return Product(
        "GRIP",
        install=(Command("GRIP's installer", installer, cwd=layout.source, timeout=3600),),
        proofs=(
            Command("the core packages import",
                    (python, str(layout.claude / "lib" / "core_requirements.py"), "check-imports")),
            Command("records can be dual-signed",
                    (python, "-c", DUAL_SIGN, str(layout.claude / "lib"))),
        ),
    )


def hal(layout: Layout) -> Product:
    clone = ("git", "clone", "--depth", "1", HAL_URL, str(layout.hal))
    return Product(
        "HAL",
        install=(Command("the clone of HAL", clone),),
        proofs=(Command("hal prints its usage", ("bash", str(layout.hal / "bin" / "hal"), "--help"),
                        expect=("Usage:",), env=(("HAL_HOME", str(layout.hal)),)),),
    )


def happi(layout: Layout) -> Product:
    return Product(
        "HAPPI",
        install=(Command("HAPPI's installer", ("bash", str(layout.hal / "happi.md"), "install"),
                         env=(("HAPPI_INSTALL_DIR", str(layout.local_bin)),)),),
        proofs=(Command("happi answers a version request",
                        ("bash", str(layout.local_bin / "happi"), "run"), stdin=VERSION_REQUEST,
                        expect=('"type":"completed"', '"text":"happi/')),),
    )


def grasp(layout: Layout) -> Product:
    python = str(layout.venv_python)
    pip = (python, "-m", "pip", "install", "--quiet", "--prefer-binary", GRASP_SPEC)
    return Product(
        "GRASP",
        install=(Command("the pip install of GRASP", pip, timeout=900),),
        proofs=(Command("GRASP imports in GRIP's venv",
                        (python, "-c", "import grasp; print('grasp', grasp.__version__)"),
                        expect=("grasp ",)),),
    )


def products(layout: Layout) -> tuple[Product, ...]:
    """In install order: HAPPI lives in HAL's folder, and GRASP in GRIP's venv."""
    return (grip(layout), hal(layout), happi(layout), grasp(layout))


def suite_steps(layout: Layout, runner: Runner = run_command,
                check: Callable[[Path], Proof] = check_computer) -> list[Step]:
    """Check the computer, then each product, in install order."""
    first = Step("Your computer", lambda: check(layout.home))
    return [first, *(as_step(product, runner) for product in products(layout))]
