"""GrokBuildHarness — xAI Grok Build CLI as a Harness leaf.

Non-interactive form: `grok -p <prompt> --sandbox <profile> --cwd <dir>
--output-format plain` (verified against grok 0.2.101 --help and
~/.grok/docs/user-guide/18-sandbox.md, 2026-07-14).

Scandal-aware defaults (the July 2026 Grok Build data-exfiltration
incident: the CLI silently bundled entire enclosing git repositories —
histories and secrets included — to an xAI storage bucket, and the
training toggle did not stop it; xAI disabled the path server-side):
this harness is QUARANTINED BY CONSTRUCTION, not by trust —

- ``cwd`` defaults to an EMPTY, non-git, PER-TASK quarantine directory
  (fresh per dispatch), so even a regressed client build has nothing to
  bundle on any run. Pointing it at a real repository is an explicit
  caller choice.
- ``sandbox`` defaults to ``strict`` (kernel-enforced: read CWD + system
  paths only, write CWD + temp). Honest limit: the child-process
  network block of read-only/strict is Linux-only — a no-op on macOS —
  so the guarantee here is filesystem scoping, not network isolation.

Auth: Grok prefers a cached session token (~/.grok/auth.json) and falls
back to ``XAI_API_KEY``. The default runner injects the key from the
macOS keychain entry ``grip-xai`` inside this shim's process scope
(syscall doctrine — callers never touch the token). Tests inject fake
runners and never read the keychain.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Optional, Sequence

from .base import HarnessResult, Task, register
from .cli import CLIHarness

_KEYCHAIN_SERVICE = "grip-xai"


def quarantine_base() -> Path:
    """User-owned quarantine root in the HOME directory — never a
    world-writable shared temp location (on Linux /tmp is shared, so a
    local attacker could pre-create or symlink the per-task path; AGORA
    round 2 on the GrokBuildHarness PR). Deliberately OUTSIDE any git
    repository. Resolved per call, never cached at import."""
    return Path.home() / ".grip-grok-quarantine"


def _keychain_xai_key() -> str:
    try:
        proc = subprocess.run(
            ["security", "find-generic-password", "-s", _KEYCHAIN_SERVICE,
             "-w"],
            capture_output=True, text=True, timeout=10)
        return proc.stdout.strip() if proc.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired):
        return ""


def _grok_env_runner(argv: Sequence[str],
                     timeout_s: float) -> tuple[int, str, str]:
    """Subprocess runner that supplies XAI_API_KEY from the keychain when
    the environment lacks it. The secret never leaves this scope."""
    env = dict(os.environ)
    if not env.get("XAI_API_KEY"):
        key = _keychain_xai_key()
        if key:
            env["XAI_API_KEY"] = key
    proc = subprocess.run(list(argv), capture_output=True, text=True,
                          timeout=timeout_s, env=env)
    return proc.returncode, proc.stdout, proc.stderr


@register
class GrokBuildHarness(CLIHarness):
    """xAI Grok Build, non-interactive, quarantined by construction."""

    KIND = "grok-build"
    EXECUTABLE = "grok"

    def __init__(self, sandbox: str = "strict",
                 cwd: Optional[Path] = None, **kwargs) -> None:
        if "runner" not in kwargs or kwargs["runner"] is None:
            kwargs["runner"] = _grok_env_runner
        super().__init__(**kwargs)
        self._sandbox = sandbox
        self._explicit_cwd = Path(cwd) if cwd else None

    def cwd_for(self, task: Task) -> Path:
        """Explicit caller cwd, or a PER-TASK quarantine subdirectory —
        fresh per dispatch, so 'empty quarantine' holds for every run,
        not only the first (one shared dir would accumulate output
        across runs and quietly weaken the guarantee)."""
        if self._explicit_cwd is not None:
            return self._explicit_cwd
        return quarantine_base() / task.id

    def as_argv(self, task: Task) -> list[str]:
        """Pure — no filesystem side effects, so composites that relay
        argv (HostedHarness) never touch disk building a command line."""
        return [self.EXECUTABLE, "-p", task.prompt,
                "--sandbox", self._sandbox,
                "--cwd", str(self.cwd_for(task)),
                "--output-format", "plain"]

    def _execute(self, task: Task) -> HarnessResult:
        self.cwd_for(task).mkdir(parents=True, exist_ok=True)
        return super()._execute(task)
