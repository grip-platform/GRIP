"""grip-moat — a four-pane terminal viewer for GRIP's cryptographic-causation moat.

One screen joins WHAT WAS DECIDED (the signed IDR envelope + its content
address), WHAT WAS BELIEVED at that moment (the belief nodes whose
``decision.records_idr`` cites this decision's content address — the one-way
belief -> decision join), WHAT IT CITED (cite.verify tallies), and ITS DER
ANCHOR (a 4-leg receipt built offline from the sovereign-council record, joined
by ``idr_id``).

The delight is that you can TRY TO FORGE IT and watch it break under the REAL
verifiers: every red is produced by the same shipped function that produced the
green a moment earlier. Nothing is simulated, and nothing is ever written —
tamper mutates in memory only.

Layout, one responsibility each:

* ``model``   — all I/O and joins; the only caller of the shipped moat libraries.
* ``render``  — pure: data in, string out, zero I/O, fully unit-testable.
* ``tamper``  — in-memory mutation + re-verification through the real functions.
* ``app``     — the key loop and the only ``print()``.

Names are resolved LAZILY (PEP 562). Importing this package therefore costs
nothing until a name is used, and one sibling module failing to import surfaces
as an error on the attribute the caller actually asked for, rather than taking
the whole package down at import time.
"""
from __future__ import annotations

from typing import Any

__version__ = "1.0.0"

# Public surface. Anything not listed is internal and may change without
# breaking callers.
__all__ = (
    "main",
    "resolve_paths",
    "MoatPaths",
    "build_index",
    "LegResult",
    "LegName",
    "VECTORS",
    "render_screen",
    "__version__",
)

# name -> sibling module that owns it.
_EXPORTS: dict[str, str] = {
    "main": "app",
    "resolve_paths": "model",
    "MoatPaths": "model",
    "build_index": "model",
    "LegResult": "model",
    "LegName": "model",
    "VECTORS": "tamper",
    "render_screen": "render",
}


def __getattr__(name: str) -> Any:
    """Resolve a public name from its owning sibling module, then cache it."""
    module_name = _EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from importlib import import_module

    value = getattr(import_module(f"{__name__}.{module_name}"), name)
    globals()[name] = value  # cache — subsequent lookups skip __getattr__
    return value


def __dir__() -> list[str]:
    return sorted(__all__)
