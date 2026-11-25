"""CLI-backed harnesses: Claude Code, Codex, OpenCode, Gemini.

Each concrete class is one external-runtime boundary (syscall doctrine:
one shim per boundary) and differs ONLY in argv shape + capability
defaults. Execution is shared in CLIHarness (DRY); the subprocess runner
is injected (DIP), so tests never spawn real CLIs.

as_argv() doubles as the CLI-invocability surface (ISP): a harness that
can render itself as a command line can be the GUEST of a HostedHarness —
which is what makes Codex-in-Claude AND Claude-in-Codex symmetric.

Capability locks (rules/untrusted-content-capability-lock.md): defaults
are the SAFE floor (claude: Read,Grep,Glob tools; codex: read-only
sandbox). Widening is an explicit constructor choice by the caller,
never implicit.
"""
from __future__ import annotations

import shutil
import os
import subprocess
from pathlib import Path
from abc import abstractmethod
from collections.abc import Mapping
from typing import Callable, Optional, Sequence

from .base import Harness, HarnessResult, Task, register
import throttle_flag  # noqa: E402  lib/ is on sys.path once .base is imported; the read-only half of the flag

RunnerFn = Callable[[Sequence[str], float], "tuple[int, str, str]"]

DEFAULT_CLAUDE_TOOLS = ("Read", "Grep", "Glob")


def _keychain(service: str) -> str:
    """Read a keychain secret, or "" when unavailable. Never raises, never logs the value."""
    try:
        out = subprocess.run(["security", "find-generic-password", "-s", service, "-w"],
                             capture_output=True, text=True, timeout=10)
        return out.stdout.strip() if out.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def credentialed_env(executable: str, base: Mapping[str, str] | None = None) -> dict:
    """os.environ plus the gateway bearer a CLI harness needs to authenticate.

    WHY. `subprocess.run` without `env=` hands the child GRIP's own environment, which in a
    live session is empty of HAL_GATEWAY_KEY — so every adapter pointing at a bare binary
    launched with no credentials and the harness refused to start. That was invisible for a
    long time because the INTERACTIVE path works: bin/grip:761-786 exports the same variables
    before exec, so `gg++ <harness>` succeeded while the identical harness called from a
    script or a fleet lane failed. A converge-fleet over all 24 harnesses found the shape
    still open for up to 19 bare adapters.

    The codex* guard is carried over verbatim from bin/grip:766-781 and is not cosmetic:
    the value is an ANTHROPIC key, and codex reads OPENAI_API_KEY as a real credential for
    its own `openai` provider, so exporting it there advertises an API-key login that fails
    on auth AND shadows the ChatGPT subscription the operator wants tried first. Prefix
    match rather than equality, deliberately, so a future `codex-next` cannot fall through.

    An absent keychain is not an error: the harness may self-authenticate from its own config
    (pi carries a literal key), and a missing value must not stop the launch.
    """
    env = dict(os.environ if base is None else base)
    # SUBSCRIPTION FIRST, API KEY AS FALLBACK. Two keychain entries hold Anthropic
    # credentials of different CLASSES: grip-claude-oauth is an sk-ant-oat subscription
    # token, grip-anthropic-external an sk-ant-api metered key. Every harness was handed
    # the metered one, which a converge-fleet reported empty of credit — so a subscription
    # the operator already pays for was reachable by no harness at all.
    #
    # Preferring OAuth is strictly better rather than a trade. On a claude-* id the gateway
    # forwards the caller's bearer verbatim to Anthropic, and since the guard was ported to
    # gateway_openai.py an sk-ant-oat token now survives that hop instead of being mangled
    # into x-api-key. On a Pool-B id the gateway discards the client bearer entirely and
    # attaches the provider's own key, so the value is a doorstop and either class passes.
    # The order therefore changes billing on exactly the requests it should and nothing else.
    key = _keychain("grip-claude-oauth") or _keychain("grip-anthropic-external")
    if key:
        env.setdefault("HAL_GATEWAY_KEY", key)
        env.setdefault("AIDER_OPENAI_API_KEY", key)
        # Strip an optional grip- shim prefix BEFORE the prefix test. Without this the
        # guard silently misses, because the adapter's EXECUTABLE is `grip-codex`, whose
        # basename starts with "grip-" rather than "codex" — so the very harness the guard
        # exists to protect was the one it let through. Caught by asserting the guard rather
        # than reading it.
        stem = Path(executable).name
        stem = stem[len("grip-"):] if stem.startswith("grip-") else stem
        if not stem.startswith("codex"):
            env.setdefault("OPENAI_API_KEY", key)
    return env


def _subprocess_runner(argv: Sequence[str],
                       timeout_s: float) -> tuple[int, str, str]:
    proc = subprocess.run(list(argv), capture_output=True, text=True,
                          env=credentialed_env(argv[0] if argv else ""),
                          timeout=timeout_s)
    return proc.returncode, proc.stdout, proc.stderr


class CLIHarness(Harness):
    """Shared subprocess execution for CLI runtimes; subclasses give argv."""

    EXECUTABLE: str = ""  # concrete subtypes declare their binary name

    def __init__(self, harness_id: Optional[str] = None, log_path=None,
                 runner: Optional[RunnerFn] = None) -> None:
        super().__init__(harness_id=harness_id, log_path=log_path)
        self._runner = runner or _subprocess_runner

    @classmethod
    def available(cls) -> bool:
        """True when the underlying binary is on PATH (runtime concern —
        substitutability is proven with injected runners regardless)."""
        return shutil.which(cls.EXECUTABLE) is not None

    @abstractmethod
    def as_argv(self, task: Task) -> list[str]:
        """Render this harness's non-interactive invocation for the task."""

    def _execute(self, task: Task) -> HarnessResult:
        rc, out, err = self._runner(self.as_argv(task), task.timeout_s)
        content = out.strip()
        if rc == 0 and content:
            return HarnessResult(id=task.id, kind=self.kind,
                                 harness_id=self.harness_id, ok=True,
                                 content=content)
        return self.fail(task, err.strip() or content
                         or f"empty output (exit {rc})")


@register
class ClaudeCodeHarness(CLIHarness):
    """Anthropic Claude Code, non-interactive (`claude -p`).

    Default tool surface is the relay's read-only allowlist. Hosting a
    guest through this harness requires Bash — an explicit opt-in:
    ClaudeCodeHarness(tools=("Bash",)).
    """

    KIND = "claude-code"
    EXECUTABLE = "claude"

    def __init__(self, tools: Sequence[str] = DEFAULT_CLAUDE_TOOLS,
                 **kwargs) -> None:
        super().__init__(**kwargs)
        self._tools = tuple(tools)

    def as_argv(self, task: Task) -> list[str]:
        """The headless argv, refused while the tick's throttle flag is set (V>>'s decision of 2026-10-29). The
        guard sits here rather than in _execute because a HostedHarness renders a Claude guest through this
        method, so one guard covers the direct run and the hosted one; run() turns the Throttled raise into an
        honest failure carrying the flag's line."""
        throttle_flag.refuse_if_set()
        return [self.EXECUTABLE, "-p", task.prompt,
                "--tools", ",".join(self._tools)]


@register
class CodexHarness(CLIHarness):
    """OpenAI Codex CLI, non-interactive (`codex exec`).

    Runs on the operator's ChatGPT subscription — Pool C, a spend pool
    separate from both Pool A (Anthropic harness) and Pool B (HAL providers).
    Moonshot's Kimi Code subscription is Pool D, never C: a different
    vendor's allowance, unarmed until the keychain entry grip-kimi-coding
    exists (rules/pool-a-offload-routing.md).
    Default sandbox is read-only; widening is the caller's explicit call.
    """

    KIND = "codex"
    # grip-codex, not bare `codex`. ~/.codex/config.toml declares
    # `env_key = "HAL_GATEWAY_KEY"` for its hal-gateway provider, so the bare binary
    # exits 1 with "Missing environment variable" before it ever reaches a model —
    # every programmatic Pool-C call failed for want of an env var. The shim reads the
    # keychain and is otherwise a drop-in taking identical argv, mirroring the way
    # OpenCodeHarness below already points at grip-opencode rather than bare opencode.
    # Measured 2026-08-31: bare codex rc=1 in 3s; via the shim rc=0 in ~5s on
    # provider hal-gateway, from an untrusted cwd with the key unset in the parent.
    EXECUTABLE = "grip-codex"

    def __init__(self, sandbox: str = "read-only", **kwargs) -> None:
        super().__init__(**kwargs)
        self._sandbox = sandbox

    def as_argv(self, task: Task) -> list[str]:
        return [self.EXECUTABLE, "exec", "--sandbox", self._sandbox,
                task.prompt]


@register
class OpenCodeHarness(CLIHarness):
    """grip-opencode non-interactive run — Pool-B (non-Anthropic) default
    model per rules/pool-a-offload-routing.md."""

    KIND = "opencode"
    EXECUTABLE = "grip-opencode"

    def __init__(self, model: str = "deepseek/deepseek-flash",
                 **kwargs) -> None:
        super().__init__(**kwargs)
        self._model = model

    def as_argv(self, task: Task) -> list[str]:
        return [self.EXECUTABLE, "run", "-m", self._model, task.prompt]


@register
class GeminiHarness(CLIHarness):
    """Google Gemini CLI, non-interactive prompt mode (`gemini -p`).

    argv shape is the boundary contract; local availability is a runtime
    concern surfaced by available(), not assumed.
    """

    KIND = "gemini"
    EXECUTABLE = "gemini"

    def as_argv(self, task: Task) -> list[str]:
        return [self.EXECUTABLE, "-p", task.prompt]
