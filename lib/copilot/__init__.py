"""Back-compat shim — ``lib/copilot`` was renamed to ``lib/aide`` (2026-06-21).

This package keeps every legacy import path working after GRIP's own founder
assistant moved from ``lib/copilot`` to ``lib/aide``:

    from lib.copilot import CopilotCore            # explicit re-export below
    from lib.copilot.core import CopilotCore       # submodule alias below
    from lib.copilot.decision_logger import ...    # submodule alias below
    from copilot.core import CopilotCore           # bare form (scripts)

It is NOT the GitHub Copilot delegator — that lives at ``lib/copilot_delegate``
(``skills/copilot-delegate`` / ``/copilot:*``) and is deliberately untouched.

Design notes:

* EXPLICIT per-symbol + per-submodule aliasing — never ``from lib.aide import *``
  (a wildcard would silently re-export private names and could not forward
  submodules anyway).
* ``CopilotCore`` is re-exported as an alias of the renamed ``AideCore``.
* The canonical package name is derived from ``__name__`` so the shim works in
  BOTH import contexts: imported as ``lib.copilot`` (cwd on path -> canonical
  ``lib.aide``) AND as the bare top-level ``copilot`` (``lib/`` on path, as the
  ``scripts/`` do -> canonical ``aide``). This avoids the bare-``copilot``
  sys.path-shadow hazard: we register the exact already-imported sibling module
  object under the legacy names, never a fresh competing package.

Reversible: delete this package to drop the shim once all callers migrate.
"""

from __future__ import annotations

import importlib
import sys

# Resolve the canonical package to forward to. Every real GRIP context puts
# ``lib/`` on ``sys.path`` (conftest.py, scripts/*, apps/aide-api/main.py), so
# the top-level ``aide`` is the primary name — pinning it means BOTH legacy
# paths (``copilot.X`` and ``lib.copilot.X``) forward to the SAME module object
# (avoids the namespace-package dual-identity where ``aide.X`` != ``lib.aide.X``).
# ``lib.aide`` is the fallback for a pure cwd-only context (``lib/`` not on path).
try:
    importlib.import_module("aide")
    _CANON = "aide"
except Exception:  # pragma: no cover - cwd-only context without lib/ on path
    importlib.import_module("lib.aide")
    _CANON = "lib.aide"

# Submodules with at least one known legacy importer (lib.copilot.X / copilot.X).
# Each is imported once from the canonical package, then registered under the
# legacy module name so `from lib.copilot.<name> import Y` and
# `from copilot.<name> import Y` resolve to the SAME object as the canonical
# `from lib.aide.<name> import Y`.
_FORWARDED_SUBMODULES = (
    "core",
    "paths",
    "company",
    "trust",
    "decision_logger",
    "decision_logger_query",
    "integrations",
    "ritual_engine",
    "workflow_runner",
    "adaptive",
    "adaptive_engine",
    "escalation",
    "agent_adapter",
    "graph",
    "brief",
    "precog",
    "key_provisioning",
    "failure_prevention",
    "atomic_io",
    "jsonl_io",
    "logger",
    "wall_relief",
    "absorb",
    "absorb.gog",
)

for _name in _FORWARDED_SUBMODULES:
    try:
        _mod = importlib.import_module(f"{_CANON}.{_name}")
    except Exception:  # pragma: no cover - optional/missing submodule stays missing
        continue
    sys.modules[f"{__name__}.{_name}"] = _mod  # e.g. lib.copilot.core
    sys.modules.setdefault(f"copilot.{_name}", _mod)  # bare copilot.<name>

# Make a bare `import copilot` resolve to this shim package too.
sys.modules.setdefault("copilot", sys.modules[__name__])

# Explicit symbol re-exports — the public class plus its legacy alias.
_core = importlib.import_module(f"{_CANON}.core")
AideCore = _core.AideCore
CopilotCore = _core.CopilotCore  # alias of AideCore, defined in <canon>.core

__all__ = ["AideCore", "CopilotCore"]
