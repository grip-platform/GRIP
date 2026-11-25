"""Import-safe facade over lib.hal_model_resolver (single source of truth).

The merged ``lib.hal_model_resolver.resolve`` never raises once imported,
but callers in arbitrary sys.path contexts (spawned launchers, the copilot
workflow runner) cannot always import it without first bootstrapping
``$CLAUDE_DIR`` onto ``sys.path``. Several callsites were copy-pasting an
identical import-safe ``_resolve_model`` wrapper — a DRY/syscall-doctrine
violation that AGORA flagged on PR #2974 ("this code duplication will
inevitably diverge"). This module is the single canonical home for that
contract: bootstrap once at *this module's* import (not per call → no
sys.path growth), expose one ``safe_resolve`` every caller shares.

Contract: ``safe_resolve(family, fallback)`` returns the catalogue value
or the verbatim ``fallback``; it NEVER raises (a missing HAL clone or
absent shim degrades to the literal so spawns/launches still work).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", str(Path.home() / ".claude")))

# Bootstrap once, at import time — not on every call (AGORA PR #2974 #3:
# per-call sys.path.insert grows duplicates / masks import issues).
if str(_CLAUDE_DIR) not in sys.path:
    sys.path.insert(0, str(_CLAUDE_DIR))

try:  # pragma: no cover - trivial import wiring
    from lib.hal_model_resolver import resolve as _resolve
except Exception:  # shim genuinely unavailable — degrade, never crash
    _resolve = None


def safe_resolve(family: str, fallback: str) -> str:
    """Resolve ``family`` from the HAL catalogue, fail-safe to ``fallback``.

    Single shared implementation (no per-callsite duplication). Returns
    ``fallback`` verbatim when the shim is unavailable or the catalogue
    lacks the family. Never raises.
    """
    if _resolve is None:
        return fallback
    try:
        return _resolve(family, fallback)
    except Exception:
        return fallback
