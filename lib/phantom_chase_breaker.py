"""Phantom-chase circuit-breaker: count investigation effort per failure hypothesis.

Origin (2026-07-14, hypothesis H798 — the RTK incident): a false "RTK is
mangling file reads" hypothesis absorbed two sessions of investigation —
iteration after iteration, an external council included — because no step ever
ran the cheap controlled test that would have killed the premise in 30
seconds. This module is the effort counter that breaks such a chase: when the
same failure-hypothesis slug accumulates more than a threshold of iterations
(default 5) with no controlled test recorded since, the verdict flips to
``"warn"`` — HALT and run the cheap ground-truth test first.

Semantics
---------
- ``record_iteration(slug, had_controlled_test, state_path)`` records one unit
  of investigation effort. A controlled test RESETS the counter to zero (the
  chase is grounded again) and marks the slug as tested; an untested iteration
  increments it. So ``iterations`` always means "iterations since the last
  controlled test".
- ``check(slug, state_path)`` reads without writing and returns
  ``{"slug", "iterations", "tested", "verdict", "reason"}`` where ``verdict``
  is ``"warn"`` once ``iterations`` EXCEEDS the threshold (strictly greater —
  at exactly the threshold it is still ``"ok"``).

State is one small JSON file mapping slug -> entry. The default lives at
``state/confab/phantom-chase.json`` under the GRIP install directory, and the
path is a parameter on every function so tests inject a temp file and never
touch real state. Advisory only — verdicts are ``"ok"``/``"warn"``, never a
block. A corrupt or missing state file reads as empty (the breaker fails open,
it never fails the caller).
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

try:  # imported as lib.phantom_chase_breaker (CLAUDE_DIR on sys.path)
    from lib.claude_dir import get_claude_dir
except ImportError:  # imported with lib/ itself on sys.path
    from claude_dir import get_claude_dir

__all__ = [
    "DEFAULT_THRESHOLD",
    "check",
    "default_state_path",
    "normalise_slug",
    "record_iteration",
]

DEFAULT_THRESHOLD = 5

_SLUG_SEPARATORS = re.compile(r"[^a-z0-9]+")


def default_state_path() -> Path:
    """The canonical state file: ``state/confab/phantom-chase.json`` under GRIP."""
    return get_claude_dir() / "state" / "confab" / "phantom-chase.json"


def normalise_slug(slug: str) -> str:
    """Lowercase-kebab a hypothesis name so "RTK Mangles Reads!" == "rtk-mangles-reads"."""
    kebab = _SLUG_SEPARATORS.sub("-", (slug or "").lower()).strip("-")
    return kebab or "unknown"


def _load(state_path: Path) -> dict:
    """The state mapping, or {} when the file is missing or unreadable (fail open)."""
    try:
        data = json.loads(Path(state_path).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(state_path: Path, data: dict) -> None:
    """Write the state mapping atomically (temp file + rename)."""
    path = Path(state_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


def _entry_for(data: dict, key: str) -> dict:
    """The slug's entry, or a fresh zero entry when absent or malformed."""
    entry = data.get(key)
    return entry if isinstance(entry, dict) else {"iterations": 0, "tested": False}


def _verdict(entry: dict, threshold: int, slug: str) -> dict:
    """Evaluate one slug entry against the threshold (warn strictly ABOVE it)."""
    iterations = int(entry.get("iterations", 0))
    tested = bool(entry.get("tested", False))
    if iterations > threshold:
        reason = (f"{iterations} investigation iterations on '{slug}' with no "
                  f"controlled test since (threshold {threshold}) — HALT and run "
                  "the cheap ground-truth test before continuing")
        return {"slug": slug, "iterations": iterations, "tested": tested,
                "verdict": "warn", "reason": reason}
    reason = f"{iterations} iteration(s) on '{slug}' within the threshold of {threshold}"
    return {"slug": slug, "iterations": iterations, "tested": tested,
            "verdict": "ok", "reason": reason}


def record_iteration(
    slug: str,
    had_controlled_test: bool,
    state_path: Path | str | None = None,
    threshold: int = DEFAULT_THRESHOLD,
) -> dict:
    """Record one investigation iteration for ``slug`` and return its fresh verdict.

    ``had_controlled_test=True`` means this iteration carried a controlled
    ground-truth test: the untested-run counter resets to zero and the slug is
    marked tested. ``False`` increments the counter. Returns the same shape as
    :func:`check`.
    """
    path = Path(state_path) if state_path else default_state_path()
    key = normalise_slug(slug)
    data = _load(path)
    entry = _entry_for(data, key)
    if had_controlled_test:
        entry["iterations"] = 0
        entry["tested"] = True
    else:
        entry["iterations"] = int(entry.get("iterations", 0)) + 1
    entry["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    data[key] = entry
    _save(path, data)
    return _verdict(entry, threshold, key)


def check(
    slug: str,
    state_path: Path | str | None = None,
    threshold: int = DEFAULT_THRESHOLD,
) -> dict:
    """Read-only verdict for ``slug``: ``{slug, iterations, tested, verdict, reason}``.

    An unknown slug reads as zero iterations, untested, verdict ``"ok"``.
    """
    path = Path(state_path) if state_path else default_state_path()
    key = normalise_slug(slug)
    return _verdict(_entry_for(_load(path), key), threshold, key)
