#!/usr/bin/env python3
"""Canonical session-identity resolution for grip-session-mesh.

Every mesh surface (the ``bin/mesh`` CLI, the register/heartbeat hook, the
inbox-poll hook, the live-dump hook, ``nextup_mesh``) keys a session by an id.
Historically that id was ``cc-<CLAUDE_SESSION_ID>`` else ``cc-pid-<ppid>``. But
the Claude Code harness exposes the stable session UUID as
**CLAUDE_CODE_SESSION_ID** (= the transcript filename stem ``<uuid>.jsonl``),
NOT ``CLAUDE_SESSION_ID`` — which is unset. So every session fell through to the
unstable bash-PPID fallback, and mesh presence rows could not be joined to the
transcript OR to a task's owner field (the lane/owner-identification gap,
task #517).

Resolution order (highest priority first):

    CLAUDE_CODE_SESSION_ID   harness UUID — the transcript stem, the join key
      -> CLAUDE_SESSION_ID    legacy / manual override
        -> cc-pid-<ppid>      last-resort, unstable (id churns per shell)

Keying on the UUID makes ``cc-<uuid>`` join directly to ``<uuid>.jsonl`` and
lets a session stamp its owner on a task with the same key.

Pure, stdlib-only, network-free: the fail-open mesh hooks (which advertise
"stdlib only") and the bash CLI mirror share the exact same contract. The
hooks keep their own inline copy of this order (they are deliberately
self-contained so a missing import can never break session start); this module
is the canonical spec that ``tests/test_mesh_identity.py`` pins every surface
against.
"""
from __future__ import annotations

import os
from typing import Mapping, Optional

# Highest-priority first. The harness UUID wins over the legacy/manual var.
ID_ENV_ORDER = ("CLAUDE_CODE_SESSION_ID", "CLAUDE_SESSION_ID")


def resolve_raw_id(env: Optional[Mapping[str, str]] = None) -> str:
    """Return the stable session id if the harness (or a manual override)
    provides one, else ``""`` (the caller supplies a PPID fallback).

    Blank / whitespace-only values are ignored so an exported-but-empty var
    never masks a lower-priority one.
    """
    src = os.environ if env is None else env
    for key in ID_ENV_ORDER:
        val = (src.get(key) or "").strip()
        if val:
            return val
    return ""


def resolve_session_id(env: Optional[Mapping[str, str]] = None,
                       ppid: Optional[int] = None) -> str:
    """Canonical mesh id: ``cc-<uuid>`` when a stable id exists, else the
    unstable ``cc-pid-<ppid>`` last resort (``ppid`` defaults to ``getppid()``).
    """
    raw = resolve_raw_id(env)
    if raw:
        return f"cc-{raw}"
    pid = os.getppid() if ppid is None else ppid
    return f"cc-pid-{pid}"
