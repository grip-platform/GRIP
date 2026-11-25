"""Core model for the stack-dogfood framework.

A :class:`Cell` is one product in one harness. A :class:`Census` is the whole
product-by-harness grid for a product. Installing is an idempotent fold:
``fold(census)`` registers every cell, ``residual(census)`` counts the cells
still missing, and the residual is zero exactly at the fixed point. That maps
the install onto ``lib/dogfood_kernel.py`` (``generic_proof``), so a complete
install is a fixed point and a re-install is a provable no-op.

Pure stdlib, no I/O. The probe/install side-effects live in ``installers.py``
and the product adapters; this module is only the data and the fold.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum


class Status(str, Enum):
    """Whether a product is wired into a harness."""

    REGISTERED = "registered"  # present and wired
    MISSING = "missing"  # absent, and installable
    UNKNOWN = "unknown"  # could not determine (harness absent, probe failed)


@dataclass(frozen=True)
class Cell:
    """One product in one harness."""

    product: str
    harness: str
    status: Status

    @property
    def key(self) -> str:
        return f"{self.product}:{self.harness}"


@dataclass(frozen=True)
class Census:
    """The product-by-harness grid for one product, in a stable order."""

    cells: tuple[Cell, ...]

    def residual(self) -> int:
        """Count of cells not yet registered — the divergence metric."""
        return sum(1 for c in self.cells if c.status is not Status.REGISTERED)

    def folded(self) -> "Census":
        """Every cell registered — the fixed point of the install fold."""
        return Census(
            tuple(replace(c, status=Status.REGISTERED) for c in self.cells)
        )

    def missing(self) -> tuple[Cell, ...]:
        """The cells a fold would change (missing or unknown), in order."""
        return tuple(c for c in self.cells if c.status is not Status.REGISTERED)


@dataclass(frozen=True)
class InstallDomain:
    """Idempotent-fold domain for ``lib/dogfood_kernel.py``.

    ``fold`` lands every cell at REGISTERED; ``residual`` is the missing count,
    zero at the fixed point. ``generic_proof(InstallDomain(), census)`` then
    witnesses whether the stack is fully installed and proves the re-install
    no-op (fold(fold(x)) == fold(x)).
    """

    zero_residual: int = 0

    def fold(self, x: Census) -> Census:
        return x.folded()

    def residual(self, x: Census) -> int:
        return x.residual()


@dataclass(frozen=True)
class Check:
    """One product-health check outcome.

    ``key`` is ``product:harness:name`` for a harness-scoped check, or
    ``product::name`` for a product-wide one.
    """

    key: str
    ok: bool
    detail: str


@dataclass(frozen=True)
class Finding:
    """A single line the always-on tick reports (see ``report.tick_findings``)."""

    key: str
    text: str


@dataclass(frozen=True)
class Report:
    """A product's dogfood report: its census plus its health checks."""

    product: str
    generated_at: str
    cells: tuple[Cell, ...]
    checks: tuple[Check, ...]

    def residual(self) -> int:
        return Census(self.cells).residual()

    def failed_checks(self) -> tuple[Check, ...]:
        return tuple(c for c in self.checks if not c.ok)

    def to_dict(self) -> dict:
        return {
            "product": self.product,
            "generated_at": self.generated_at,
            "cells": [
                {"product": c.product, "harness": c.harness, "status": c.status.value}
                for c in self.cells
            ],
            "checks": [
                {"key": c.key, "ok": c.ok, "detail": c.detail} for c in self.checks
            ],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Report":
        cells = tuple(
            Cell(c["product"], c["harness"], Status(c["status"]))
            for c in d.get("cells", [])
        )
        checks = tuple(
            Check(c["key"], bool(c["ok"]), c.get("detail", ""))
            for c in d.get("checks", [])
        )
        return cls(d["product"], d.get("generated_at", ""), cells, checks)
