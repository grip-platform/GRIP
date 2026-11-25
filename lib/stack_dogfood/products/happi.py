"""HAPPI product adapter.

HAPPI is a protocol with a runtime (``~/.hal/HAPPI.md install`` puts ``happi`` on
PATH). "Installed in every harness" means every harness can shell out to that
runtime, so the install surface is a single PATH cell rather than a per-harness
MCP registration. The health checks round-trip a real version envelope and
confirm the HAL runtime source is present.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

from ..installers import Target, which_target
from ..model import Check
from ..run import run

NAME = "happi"
_INSTALL_NOTE = "run: bash ~/.hal/HAPPI.md install"


def _hal_home() -> Path:
    return Path(os.environ.get("HAL_HOME", str(Path.home() / ".hal")))


def targets() -> list[Target]:
    return [which_target("path", "happi", note=_INSTALL_NOTE)]


def _version_check() -> Check:
    happi = shutil.which("happi") or str(Path("~/.local/bin/happi").expanduser())
    envelope = '{"v":"happi/1.6","id":"check","cmd":"version"}'
    res = run([happi, "run"], timeout=15, input_text=envelope + "\n")
    ok = res.ok and '"type":"completed"' in res.out
    first = next((ln for ln in res.out.splitlines() if ln.strip()), "")
    detail = (first or res.err.strip() or "no output")[:120]
    return Check(f"{NAME}::version-envelope", ok, detail)


def _runtime_check() -> Check:
    runtime = _hal_home() / "HAPPI.md"
    return Check(f"{NAME}::hal-runtime", runtime.exists(), str(runtime))


def checks() -> list[Check]:
    return [_version_check(), _runtime_check()]
