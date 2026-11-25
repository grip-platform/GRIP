"""DshHarness — the DeepSeek Harness (dsh) CLI as a Harness leaf.

Non-interactive form: `dsh --profile headless <prompt>` (verified
2026-08-19 against the local checkout at ~/deepseek-harness, dsh
0.1.0-rc.7 — confidence HIGH, read from source and reproduced live).

Provenance (source paths read, not recalled):
- apps/cli/src/args.ts:127-131 — the root command carries
  `.allowUnknownOption().passThroughOptions().enablePositionalOptions()`
  with `--profile <name>` as a LAUNCHER option and `[args...]` as the
  pass-through positional. Launcher flags therefore come first, and the
  first token the launcher does not recognise starts the app's own args.
- packages/bundle/headless/src/startup.ts — `headlessCommand()` declares
  `.argument('[task...]', ...)` and its action does `program.args.join(' ')`.
  A single argv element is returned unchanged by join, so a multi-word or
  multi-line prompt passed as ONE element survives verbatim.
- packages/bundle/headless/src/index.ts:129 — `io.stdout.write(outcome.text
  + '\\n')` is the ONLY stdout write; errors go to stderr (:86, :131) and
  :133 exits 0 only when the final reason is `completed`. Stdout is
  therefore clean enough for the base CLIHarness._execute contract, and no
  `_execute` override is needed here.

Capability floor (rules/untrusted-content-capability-lock.md): unlike
goose, dsh HAS a real read-only mode — but it is an environment variable,
not a flag. packages/bundle/base/cordis.patch.yml:175 resolves the sandbox
as `process.env.DSH_PERMISSION_MODE ?? 'workspace-write'`, and :191 makes
tool approval `never` only under `danger-full-access`. The default runner
therefore pins `DSH_PERMISSION_MODE=read-only`; widening is an explicit
constructor choice, `DshHarness(permission_mode="workspace-write")`.

Design note — why this sets the variable UNCONDITIONALLY, where goose's
`_goose_env_runner` uses `setdefault`. GOOSE_MODE is a FUNCTIONAL
requirement (headless goose refuses to run without it), so deferring to an
ambient value there is harmless. DSH_PERMISSION_MODE is a SECURITY floor:
headless dsh runs fine without it, just at `workspace-write`. Under
`setdefault`, an operator's exported `DSH_PERMISSION_MODE=danger-full-access`
would silently lower the floor for every GRIP dispatch — and a floor an
ambient variable can lower is not a floor. The value is read from
`self._permission_mode`, so the runner is built per-instance rather than
being a module-level constant like goose's.

Honest limits (stated, never papered over):
- A prompt whose FIRST character is a dash is rejected before the agent
  runs: `headlessCommand()` never calls `allowUnknownOption()`, so
  commander treats it as a flag. Reproduced 2026-08-19 — `dsh --profile
  headless "--fix the build"` exits 1 with empty stdout and `error:
  unknown option '--fix the build'` on stderr, while the control `"fix the
  --build flag"` boots normally. A `--` separator does not rescue it.
  The failure is honest (ok=False carrying that stderr), so nothing is
  corrupted; as_argv does NOT mutate the operator's prompt to work around
  it (rules/no-stubs.md — faking an absent upstream capability).
- Every dispatch persists a session under `$DSH_HOME/sessions/`, carrying
  the prompt and the model's output. `session-persistence-jsonl` is a
  dsh-base row and the headless profile does not disable it; there is no
  `--no-session` equivalent. A high-frequency loop accumulates these.
- Booting the Cordis tree costs ~5.6s per dispatch before any model
  latency — cheap against the 300s default Task.timeout_s, but a poor fit
  for high-fanout dispatch of many short tasks.
- Stdout purity is proven for macOS/Linux. One Windows-only path
  (packages/sandbox/sandbox-windows-acl) takes full `stdio: 'inherit'`;
  whether a Windows tool run reaches it is UNVERIFIED.

Tests inject fake runners and never spawn dsh.
"""
from __future__ import annotations

import os
import subprocess
from typing import Sequence

from .base import Task, register
from .cli import CLIHarness, RunnerFn

DEFAULT_DSH_PERMISSION_MODE = "read-only"


def make_dsh_env_runner(permission_mode: str) -> RunnerFn:
    """Build a subprocess runner pinning DSH_PERMISSION_MODE for this harness.

    Set unconditionally, never `setdefault`: the variable IS the sandbox
    floor (base cordis.patch.yml:175), so honouring an ambient value would
    let the environment widen a capability the caller never asked to widen.
    """

    def run(argv: Sequence[str], timeout_s: float) -> tuple[int, str, str]:
        env = dict(os.environ)
        env["DSH_PERMISSION_MODE"] = permission_mode
        proc = subprocess.run(list(argv), capture_output=True, text=True,
                              timeout=timeout_s, env=env)
        return proc.returncode, proc.stdout, proc.stderr

    return run


@register
class DshHarness(CLIHarness):
    """DeepSeek Harness, headless one-shot (`dsh --profile headless`)."""

    KIND = "dsh"
    # grip-dsh, not bare `dsh`. The launch registry already re-routes dsh to the shim
    # (grip_harness_picker.py:152) because the shim exports the gateway bearer from the
    # keychain; this adapter did not, so the programmatic path called a binary with no
    # credentials while the interactive path worked — the same asymmetry that made Pool C
    # unreachable from code. Found by a converge-fleet over all 24 harnesses, 2026-08-31.
    EXECUTABLE = "grip-dsh"

    def __init__(self, permission_mode: str = DEFAULT_DSH_PERMISSION_MODE,
                 **kwargs) -> None:
        if kwargs.get("runner") is None:
            kwargs["runner"] = make_dsh_env_runner(permission_mode)
        super().__init__(**kwargs)
        self._permission_mode = permission_mode

    def as_argv(self, task: Task) -> list[str]:
        return [self.EXECUTABLE, "--profile", "headless", task.prompt]
