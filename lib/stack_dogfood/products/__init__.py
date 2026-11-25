"""Product adapters for the stack-dogfood framework.

One module per product. Each exposes the same tiny surface:

* ``NAME: str``
* ``targets() -> list[installers.Target]`` — the install cells (what to probe,
  and optionally how to install), in a stable order.
* ``checks() -> list[model.Check]`` — product-health checks beyond mere
  registration.

Each product declares the install surface that actually fits it, not a forced
uniform grid: GRASP registers as an MCP server per harness, HAPPI is a runtime
on PATH, HAL is the gateway plus harness wiring, GRIP is the harness itself.
"""
from __future__ import annotations

from importlib import import_module

NAMES = ("grasp", "happi", "hal", "grip")


def load(name: str):
    """Import and return the adapter module for ``name``."""
    if name not in NAMES:
        known = ", ".join(NAMES)
        raise ValueError(f"unknown product {name!r}; known products: {known}")
    return import_module(f"{__name__}.{name}")
