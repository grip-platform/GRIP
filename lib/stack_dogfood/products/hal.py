"""HAL product adapter.

HAL is the multi-provider gateway (port 4010) plus the ``grip-harness`` wiring
that points each harness at it. Its install is probe-only here: HAL wiring needs
a dry run shown to the operator before it is applied, so the adapter reports
state and never auto-installs.
"""
from __future__ import annotations

import os
from pathlib import Path

from ..installers import Target
from ..model import Check, Status
from ..run import TIMEOUT_MISSING, run

NAME = "hal"
_GATEWAY_URL = "http://127.0.0.1:4010/health"


def _hal_home() -> Path:
    return Path(os.environ.get("HAL_HOME", str(Path.home() / ".hal")))


def _gateway_probe() -> "tuple[Status, str]":
    res = run(["curl", "-sf", "-m", "5", _GATEWAY_URL], timeout=10)
    if res.ok:
        return Status.REGISTERED, "gateway 4010 healthy"
    if res.code == TIMEOUT_MISSING:
        return Status.UNKNOWN, "curl not found"
    return Status.MISSING, f"gateway 4010 unreachable (curl exit {res.code})"


def targets() -> list[Target]:
    return [Target("gateway", _gateway_probe, None)]


def checks() -> list[Check]:
    home = _hal_home()
    return [Check(f"{NAME}::home", (home / "HAPPI.md").exists(), str(home))]
