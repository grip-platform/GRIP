"""KimiCodeHarness — Moonshot's Kimi Code CLI (``kimi``) as a Harness leaf.

Non-interactive form: ``kimi -p <prompt>``, dispatched through the
``bin/grip-kimi`` shim so the HAL gateway route is in the CLI's config before
it starts (mirrors ``CodexHarness.EXECUTABLE = "grip-codex"`` and
``DshHarness.EXECUTABLE = "grip-dsh"``). Harness in a harness in both
directions: GRIP dispatches kimi here, and kimi uses GRIP's gateway as its
provider (lib/kimi_hal_route.py).

Provenance — vendor documentation read AS TEXT on 2026-09-07. The CLI is NOT
installed on the machine that wrote this, so nothing below was reproduced
live; each claim carries its own confidence. Line numbers index the
plain-text render of each page (the fleet corpus mirror
``kimi-code-docs/*.txt``), not the HTML source: ``:55`` is the 55th line of
the page read as text.

- https://www.kimi.com/code/docs/en/kimi-code-cli/getting-started.html
  :55-56  "To run a single instruction without entering the interactive UI,
          use -p:  kimi -p "Take a look at this project's directory structure""
          -> the argv shape. HIGH that ``-p <prompt>`` is the headless entry;
          UNVERIFIED-until-installed that the process exits after one answer
          and that stdout carries only the answer (CLIHarness._execute takes
          stdout as the content, so a banner would ride along — noisy, not
          wrong). ``bin/grip-kimi-verify`` observes both on first install.
  :42     install ``curl -fsSL https://code.kimi.com/kimi-code/install.sh | bash``
  :49     or ``npm install -g @moonshot-ai/kimi-code`` (Node 22.19.0+, :47).
  :70     "Read-only operations are executed automatically by default without
          requiring confirmation. For operations that modify files or run
          shell commands, it asks for your confirmation before proceeding."
  :94     data under ``~/.kimi-code/``; ``KIMI_CODE_HOME`` relocates it.
  :97     ``kimi --version`` is the documented install check.
- https://www.kimi.com/code/docs/en/kimi-code-cli/configuration/providers.html
  :41     capabilities are "matched automatically by model name prefix".
  :42     "The CLI does not fall back to shell environment variables for
          credentials" — why the route file carries the gateway doorstop in
          ``api_key`` and this runner pins no credential at all.
- https://www.kimi.com/code/docs/en/kimi-code-cli/reference/kimi-acp.html
  :30     ``kimi acp`` — JSON-RPC over stdin/stdout for IDE clients. A future
          HostedHarness/ACP seam, deliberately not used here.
- https://code.kimi.com/kimi-code/install.sh (read as text, never executed)
  :13,:541  installs ONE native binary to ``$KIMI_INSTALL_DIR/bin/kimi``
            (default ``~/.kimi-code/bin/kimi``); :512-532 fetch the manifest,
            download, verify sha256 before installing.

Capability floor (rules/untrusted-content-capability-lock.md). The mirrored
docs document NO per-invocation read-only flag and NO environment variable
that narrows tool approval; the most restrictive documented mode is the
DEFAULT — confirm before file writes and shell commands (getting-started:70).
Unlike DshHarness, which pins ``DSH_PERMISSION_MODE=read-only``, there is
therefore nothing to pin: ``as_argv`` passes exactly ``-p <prompt>`` and no
switch that could widen (no auto-approve flag is documented; none would be
passed if one were). How a headless ``-p`` run resolves a confirmation with
no TTY — refuse, hang, or proceed — is UNVERIFIED-until-installed and is
exactly what ``bin/grip-kimi-verify`` reports (a run that does not exit inside
its timeout is FAIL with that diagnosis, never silence). Durable deny rules do
exist (``[[permission.rules]]`` in config.toml, mcp.html:80-88), but the
mirror shows their pattern syntax only for ``mcp__<server>__<tool>`` names, so
writing them for built-in tools would be an invented shape (rules/no-stubs.md)
and is left to the operator.

Honest limits:
- Whether a prompt whose first character is a dash survives ``-p`` is
  UNVERIFIED (dsh rejects one; kimi's parser is unread). as_argv never mutates
  the operator's prompt to work around a parser it has not seen.
- Which model answers is the CLI's current selection. The HAL route makes
  ``kimi-k3-hal`` / ``kimi-k2.7-code-hal`` selectable (``/model``). Across the
  four mirrored CLI pages the whole argv surface is ``-p``, ``-c`` and
  ``--version`` plus the ``acp``, ``provider`` and ``upgrade`` subcommands
  (getting-started :55-58, :97-98; providers :55; kimi-acp :31): no flag sets
  model, effort or thinking, and no default-model key is documented, so
  nothing here can force it, and nothing pretends to. The one programmatic
  model/thinking switch in the corpus is ACP's ``session/set_config_option``
  (kimi-acp :71) — the future seam, not this one.
- ``available()`` asks PATH for the SHIM, which GRIP's bin always ships;
  whether ``kimi`` itself is installed is answered by the shim (exit 127 with
  the vendor's install line) and by ``grip-kimi-verify``.

Tests inject fake runners and never spawn kimi.
"""
from __future__ import annotations

from .base import Task, register
from .cli import CLIHarness


@register
class KimiCodeHarness(CLIHarness):
    """Moonshot Kimi Code CLI, headless one-shot (``kimi -p``) via bin/grip-kimi."""

    KIND = "kimi-code"
    # grip-kimi, not bare `kimi`: the shim refuses with a plain-English 127 when
    # the vendor binary is absent, keeps ~/.kimi-code/config.toml carrying the
    # HAL route, and exports the gateway doorstop — the same asymmetry fix that
    # points CodexHarness at grip-codex and DshHarness at grip-dsh.
    EXECUTABLE = "grip-kimi"

    def as_argv(self, task: Task) -> list[str]:
        return [self.EXECUTABLE, "-p", task.prompt]
