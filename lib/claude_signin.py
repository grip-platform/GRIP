"""Shared Claude Code sign-in resolution for headless/launchd children.

launchd does not read the shell profile, where GRIP's installer exports
``CLAUDE_CONFIG_DIR`` as the GRIP folder (``scripts/blackbook``). Claude Code
keeps one sign-in per folder PATH, and ``~/.claude`` is a link to that folder
under a different path, so a headless child spawned under launchd finds
another, older sign-in or none unless it is told which folder to use: every
``bin/grip-security-heal`` dispatch failed this way from 2026-10-22
(GRIP#8918), fixed by GRIP#9388 (merge commit ``bd129929b``). This module is
the shared home for that fix, so a second unattended caller --
``lib/stack_dogfood/escalation.py``'s ``agent_env`` -- gets the same
resolution without duplicating it.

``signin_folder`` is the entry point. The rules it enforces:

* A caller that already chose a folder or credential (``CLAUDE_CONFIG_DIR``,
  ``ANTHROPIC_API_KEY``, ``ANTHROPIC_AUTH_TOKEN``, ``CLAUDE_CODE_OAUTH_TOKEN``)
  is left exactly as it is.
* A working INHERITED sign-in is kept -- a machine that never needed this
  resolution runs exactly as before, because setting ``CLAUDE_CONFIG_DIR``,
  even to the default folder, can switch Claude Code to a different stored
  sign-in.
* Otherwise the realpath of the caller's own GRIP folder is used, and only
  when ``claude auth status`` reports a sign-in there.
"""
from __future__ import annotations

import json
import os
import subprocess

AUTH_PROBE_TIMEOUT_S = 60  # one `claude auth status` question
# A variable that already chooses the child's Claude folder or credential. With
# one set, the caller's own choice is left exactly as it is.
CALLER_CHOSEN_VARS = (
    "CLAUDE_CONFIG_DIR",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "CLAUDE_CODE_OAUTH_TOKEN",
)


def signed_in(env: dict, *, timeout: float = AUTH_PROBE_TIMEOUT_S) -> bool:
    """Whether `claude auth status` reports a sign-in under this environment.

    Only a clear yes counts. No claude, a timeout, or output that is not the
    JSON object the command prints all mean "not signed in".
    """
    try:
        cp = subprocess.run(["claude", "auth", "status"], env=env, capture_output=True,
                            text=True, timeout=timeout)
        status = json.loads(cp.stdout or "")
    except (OSError, subprocess.SubprocessError, ValueError):
        return False
    return isinstance(status, dict) and status.get("loggedIn") is True


def signin_folder(env: dict, claude_dir: str | os.PathLike) -> tuple[str | None, str]:
    """The Claude folder (``CLAUDE_CONFIG_DIR``) to give the child, and why.

    ``claude_dir`` is the caller's own GRIP folder (e.g. ``get_claude_dir()``
    or a module-level ``CLAUDE_DIR`` constant) -- resolved by the caller,
    never hardcoded here, so this module stays portable across installs and
    testable with a ``tmp_path``. It must already name a concrete directory:
    a caller whose own ``claude_dir`` may be ``None`` resolves that itself
    first (e.g. ``claude_dir or get_claude_dir()``), since this function
    passes it straight to ``os.path.realpath``.

    The GRIP folder is asked about only when the environment it was given has
    no working sign-in already: a machine that never needed this resolution
    runs exactly as before, because setting the variable, even to the default
    folder, can switch Claude Code to a different stored sign-in.

    Returns ``(None, "caller")`` when the caller already chose a folder or
    credential; ``(None, "inherited")`` when the given environment already
    has a working sign-in; ``(realpath(claude_dir), "grip-folder")`` when
    that folder has one; ``(None, "none")`` when neither does.
    """
    if any((env.get(name) or "").strip() for name in CALLER_CHOSEN_VARS):
        return None, "caller"
    if signed_in(env):
        return None, "inherited"
    folder = os.path.realpath(claude_dir)
    if signed_in({**env, "CLAUDE_CONFIG_DIR": folder}):
        return folder, "grip-folder"
    return None, "none"
