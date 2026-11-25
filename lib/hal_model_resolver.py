"""HAL model-id resolver — single source of truth for default model literals.

Syscall-doctrine shim (N=3 extraction). The canonical precedent is
``~/.claude/bin/agora-demo::_default_model()`` (callsite #1, shipped);
``scripts/agora-agentmail-council.py`` was callsite #2; this module is the
N=3 promotion so every GRIP default-model literal resolves from HAL's
canonical catalogue instead of going stale invisibly.

Authority model (resolved 2026-05-19 from canonical evidence):

- ``~/.hal/config/model_catalog.yaml`` is authoritative for *tier-keyed
  family resolution* — it lists ``- address: <provider>/<model>`` entries
  with the primary tier first per family. This is exactly what the
  agora-demo precedent reads.
- A resolve **never raises**. If the catalogue is absent (HAL not cloned),
  unreadable, or has no matching row, the caller-supplied fail-safe literal
  is returned. A missing HAL clone degrades to a working default, never a
  crash. This is the same fail-safe contract as the precedent.

Design constraints honoured: KISS (pure stdlib line-scan, no yaml dep — the
precedent deliberately avoids importing yaml so it works before deps are
installed), DRY (one resolver for every GRIP callsite), BIG-O O(n) single
pass over a small config file.
"""
from __future__ import annotations

import os
from pathlib import Path

_HOME = Path(os.environ.get("HOME", str(Path.home())))
_CATALOGUE = _HOME / ".hal" / "config" / "model_catalog.yaml"


def resolve(family: str, fallback: str) -> str:
    """Return the first ``model_catalog.yaml`` address for ``family``.

    ``family`` is a bare-id prefix that identifies the wanted tier within a
    provider, e.g. ``anthropic/claude-opus-`` (primary Opus),
    ``anthropic/claude-sonnet-`` (Sonnet tier),
    ``anthropic/claude-haiku-`` (Haiku tier), ``gemini/gemini-2.5-flash``
    (mid Gemini), ``gemini/gemini-3.1-pro`` (frontier Gemini).

    The catalogue lists the primary entry of each family first, so the
    first matching ``- address:`` line is the canonical default. Returns
    the bare model id (provider prefix stripped) — matching the precedent's
    return shape (``anthropic/claude-opus-4-7`` -> ``claude-opus-4-7``).

    ``fallback`` is returned verbatim (never mutated, never stripped) when
    the catalogue is unreadable or has no matching row. Never raises.
    """
    try:
        for line in _CATALOGUE.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith("- address:") and family in stripped:
                address = stripped.split("address:", 1)[1].strip()
                # Strip the provider prefix to match the precedent's shape.
                return address.split("/", 1)[1] if "/" in address else address
    except OSError:
        pass
    return fallback


def resolve_address(family: str, fallback: str) -> str:
    """Like :func:`resolve` but returns the full ``provider/model`` address.

    Used by callsites whose dispatch path expects an HAL-style address
    (e.g. ``anthropic/claude-haiku-4-5``) rather than a bare model id.
    ``fallback`` is returned verbatim when the catalogue is unavailable.
    Never raises.
    """
    try:
        for line in _CATALOGUE.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith("- address:") and family in stripped:
                return stripped.split("address:", 1)[1].strip()
    except OSError:
        pass
    return fallback
