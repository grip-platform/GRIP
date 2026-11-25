#!/usr/bin/env python3
"""Prove an installed Kimi Code CLI boots headless through GRIP's HAL route.

"Verify a profile can actually boot, not merely compose" (GRIP#5010, the DSH
lesson). lib/kimi_hal_route.py composes a config; nothing about a composed file
proves the CLI accepts it, exits after one ``-p`` answer, or reaches the
gateway. This probe runs three bounded steps and reports each in plain words:

  version   ``kimi --version`` (getting-started.html:97). NOT-INSTALLED when the
            binary is absent — and then nothing else runs.
  route     ``~/.kimi-code/config.toml`` parses and carries ``[providers.hal]``
            with the expected type and base_url plus every GRIP alias: the
            composed half, checked against the file the CLI will actually read.
  headless  ``kimi -p "<trivial prompt>"`` (getting-started.html:55-56) under a
            timeout. PASS on exit 0 with non-empty stdout; FAIL on a non-zero
            exit, on empty output, or on a run that never exits — the
            UNVERIFIED-until-installed point: a ``-p`` run waiting on a
            confirmation prompt, or one that fell into the TUI, looks exactly
            like a hang, and it must read as FAIL with that reason, never as
            silence.

What it cannot prove, and says so: WHICH model answered. The mirrored docs
document no ``--model`` flag and no default-model key, so the headless step
runs on the CLI's current selection; the report tells the operator to pick a
``-hal`` alias in ``/model`` to route through the gateway. Every external call
is injectable, so the tests never spawn a binary.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from enum import IntEnum
from pathlib import Path
from typing import Callable, Sequence

_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))
from kimi_hal_route import (  # noqa: E402
    BASE_URL,
    PROVIDER,
    PROVIDER_TYPE,
    default_config_path,
    model_tables,
)

RunnerFn = Callable[[Sequence[str], float], "tuple[int, str, str]"]

BINARY = "kimi"
HEADLESS_PROMPT = "Reply with the single word OK and nothing else."
INSTALL_LINE = "curl -fsSL https://code.kimi.com/kimi-code/install.sh | bash"


class ProbeStatus(IntEnum):
    """Exit codes: the three outcomes an operator can act on."""

    PASS = 0
    FAIL = 1
    NOT_INSTALLED = 2


_WORDS = {ProbeStatus.PASS: "PASS", ProbeStatus.FAIL: "FAIL",
          ProbeStatus.NOT_INSTALLED: "NOT-INSTALLED"}


@dataclass(frozen=True)
class StepResult:
    name: str
    status: ProbeStatus
    detail: str


def default_runner(argv: Sequence[str], timeout_s: float) -> tuple[int, str, str]:
    """Real subprocess runner. Raises FileNotFoundError / TimeoutExpired like subprocess does."""
    proc = subprocess.run(list(argv), capture_output=True, text=True, timeout=timeout_s)
    return proc.returncode, proc.stdout, proc.stderr


def _head(*texts: str) -> str:
    """First non-blank line of the first non-blank text, capped — for a report line."""
    for text in texts:
        lines = text.strip().splitlines()
        if lines:
            return lines[0][:200]
    return ""


def probe_version(runner: RunnerFn = default_runner, timeout_s: float = 20.0) -> StepResult:
    try:
        rc, out, err = runner([BINARY, "--version"], timeout_s)
    except FileNotFoundError:
        return StepResult("version", ProbeStatus.NOT_INSTALLED,
                          f"{BINARY} is not installed (not on PATH). Vendor install: {INSTALL_LINE}")
    except subprocess.TimeoutExpired:
        return StepResult("version", ProbeStatus.FAIL,
                          f"`{BINARY} --version` did not exit within {timeout_s:g}s")
    if rc != 0:
        return StepResult("version", ProbeStatus.FAIL,
                          f"`{BINARY} --version` exited {rc}: {_head(err, out) or 'no output'}")
    return StepResult("version", ProbeStatus.PASS, _head(out, err) or "exited 0 with no version text")


def _route_problems(doc: dict) -> list[str]:
    providers = doc.get("providers")
    hal = providers.get(PROVIDER) if isinstance(providers, dict) else None
    if not isinstance(hal, dict):
        return [f"[providers.{PROVIDER}] is missing"]
    problems = [f"{key} is {hal.get(key)!r}, expected {want!r}"
                for key, want in (("type", PROVIDER_TYPE), ("base_url", BASE_URL))
                if hal.get(key) != want]
    models = doc.get("models") if isinstance(doc.get("models"), dict) else {}
    missing = [alias for alias, table in model_tables().items() if models.get(alias) != table]
    if missing:
        problems.append(f"alias table(s) missing or altered: {missing}")
    return problems


def probe_route(config_path: Path | None = None) -> StepResult:
    path = config_path or default_config_path()
    if not path.is_file():
        return StepResult("route", ProbeStatus.FAIL,
                          f"{path} does not exist — run grip-kimi once to write the hal route")
    try:
        doc = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        return StepResult("route", ProbeStatus.FAIL, f"{path} could not be read as TOML: {exc}")
    problems = _route_problems(doc)
    if problems:
        return StepResult("route", ProbeStatus.FAIL,
                          "; ".join(problems) + f" — re-run grip-kimi to restore the block in {path}")
    # Name the file on PASS as well as on FAIL: an operator with several
    # KIMI_CODE_HOMEs must see which config.toml the probe read (GRIP#5346).
    return StepResult("route", ProbeStatus.PASS,
                      f"{path}: [providers.{PROVIDER}] -> {BASE_URL} ({PROVIDER_TYPE}); "
                      f"aliases {sorted(model_tables())}")


def probe_headless(runner: RunnerFn = default_runner, timeout_s: float = 90.0) -> StepResult:
    try:
        rc, out, err = runner([BINARY, "-p", HEADLESS_PROMPT], timeout_s)
    except FileNotFoundError:
        return StepResult("headless", ProbeStatus.NOT_INSTALLED, f"{BINARY} is not installed")
    except subprocess.TimeoutExpired:
        return StepResult("headless", ProbeStatus.FAIL,
                          f"`kimi -p` did not exit within {timeout_s:g}s and was killed. A headless "
                          "run that waits on a confirmation prompt, or one that fell into the "
                          "interactive TUI, looks exactly like this.")
    if rc != 0:
        return StepResult("headless", ProbeStatus.FAIL,
                          f"`kimi -p` exited {rc}: {_head(err, out) or 'no output'}")
    if not out.strip():
        return StepResult("headless", ProbeStatus.FAIL,
                          "`kimi -p` exited 0 with empty stdout — the answer did not reach stdout")
    return StepResult("headless", ProbeStatus.PASS, f"answered: {_head(out)}")


def run_all(runner: RunnerFn = default_runner, config_path: Path | None = None,
            timeout_s: float = 90.0) -> list[StepResult]:
    """version, then route and headless — unless the binary is absent."""
    results = [probe_version(runner)]
    if results[0].status is ProbeStatus.NOT_INSTALLED:
        return results
    results.append(probe_route(config_path))
    results.append(probe_headless(runner, timeout_s))
    return results


def overall(results: Sequence[StepResult]) -> ProbeStatus:
    statuses = {result.status for result in results}
    if ProbeStatus.NOT_INSTALLED in statuses:
        return ProbeStatus.NOT_INSTALLED
    return ProbeStatus.FAIL if ProbeStatus.FAIL in statuses else ProbeStatus.PASS


def render(results: Sequence[StepResult]) -> str:
    lines = [f"[grip-kimi-verify] {result.name:<9}{_WORDS[result.status]}: {result.detail}"
             for result in results]
    verdict = overall(results)
    lines.append(f"[grip-kimi-verify] overall  {_WORDS[verdict]}")
    if verdict is ProbeStatus.PASS:
        lines.append("[grip-kimi-verify] note: the headless step ran on the CLI's CURRENT model "
                     "selection; the vendor docs document no --model flag, so pick a -hal alias "
                     "in /model (e.g. kimi-k3-hal) to route through the HAL gateway.")
    return "\n".join(lines) + "\n"


def main(argv: Sequence[str] | None = None, runner: RunnerFn = default_runner,
         config_path: Path | None = None) -> int:
    """CLI entry for ``grip-kimi-verify``. No arguments; GRIP_KIMI_VERIFY_TIMEOUT bounds the headless run."""
    args = list(sys.argv[1:] if argv is None else argv)
    if args:
        print("usage: grip-kimi-verify   (no arguments; GRIP_KIMI_VERIFY_TIMEOUT=<seconds> "
              "bounds the headless run, default 90)", file=sys.stderr)
        return int(ProbeStatus.FAIL)
    try:
        timeout_s = float(os.environ.get("GRIP_KIMI_VERIFY_TIMEOUT", "90"))
    except ValueError:
        timeout_s = 90.0
    results = run_all(runner, config_path, timeout_s)
    print(render(results), end="")
    return int(overall(results))


if __name__ == "__main__":
    raise SystemExit(main())
