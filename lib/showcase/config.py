"""Showcase per-project config loader with mtime hot-reload.

Reads `state/showcases/<project>/config.json`. Caches the parsed dict keyed
by mtime so a config edit takes effect on the next request without restart.
"""
from __future__ import annotations

import json
from pathlib import Path

from .log import SHOWCASES_DIR

_CACHE: dict[str, tuple[float, dict]] = {}


def config_path(project: str) -> Path:
    return SHOWCASES_DIR / project / "config.json"


def load_config(project: str) -> dict | None:
    """Load + cache per-project config. Returns None if missing.

    Hot-reload: re-reads the JSON when `config.json` mtime changes.
    """
    cfg = config_path(project)
    if not cfg.exists():
        return None
    mtime = cfg.stat().st_mtime
    cached = _CACHE.get(project)
    if cached and cached[0] == mtime:
        return cached[1]
    data = json.loads(cfg.read_text())
    _CACHE[project] = (mtime, data)
    return data


def list_projects() -> list[str]:
    """All projects with a `config.json` present (sorted, leading `_` skipped)."""
    if not SHOWCASES_DIR.exists():
        return []
    return sorted(
        p.name for p in SHOWCASES_DIR.iterdir()
        if p.is_dir() and not p.name.startswith("_") and (p / "config.json").exists()
    )
