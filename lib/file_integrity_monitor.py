"""File Integrity Monitor — fail-closed SHA256/mtime/size tracking.

Opt-in registration of arbitrary file paths. Persistent registry at
state/security/file-integrity-registry.json. Verifies registered files
have not changed unexpectedly. No external network/IO boundaries
crossed; pure stdlib (hashlib, json, os).

Public API:
    register(path) -> dict      register a file (records SHA256/mtime/size)
    unregister(path) -> bool    remove from registry
    verify_all() -> list[dict]  verify all registered entries, return failures
    refresh(path) -> dict       update stored hash after legitimate change
    list_registered() -> list   list all registered paths + metadata

Fail-closed: any mismatch counts as a verify failure (no auto-heal).
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any


# Registry path — relative to repo root (parent of this lib/ directory)
_REPO_ROOT = Path(__file__).resolve().parent.parent
REGISTRY_PATH = _REPO_ROOT / "state" / "security" / "file-integrity-registry.json"

_CHUNK = 65536


def _hash_file(path: Path) -> str:
    """Compute SHA256 of a file, streamed in chunks."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(_CHUNK)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _stat_triplet(path: Path) -> dict[str, Any]:
    """Return SHA256 + mtime_ns + size for a file."""
    st = os.stat(path)
    return {
        "sha256": _hash_file(path),
        "mtime_ns": st.st_mtime_ns,
        "size": st.st_size,
    }


def _validate_target(path: Path) -> None:
    """Reject anything that is not a regular existing file."""
    if not path.exists():
        raise FileNotFoundError(f"path does not exist: {path}")
    if not path.is_file():
        raise ValueError(f"not a regular file: {path}")


def _resolve_registry_path(registry_path: Path | None) -> Path:
    """Resolve registry path at call time so tests can monkeypatch the module."""
    if registry_path is not None:
        return registry_path
    import sys as _sys
    return _sys.modules[__name__].REGISTRY_PATH


def _load_registry(registry_path: Path | None = None) -> dict[str, dict[str, Any]]:
    """Load registry. Returns {} on missing/corrupt — caller decides policy."""
    rp = _resolve_registry_path(registry_path)
    if not rp.exists():
        return {}
    try:
        with open(rp, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {}
        return data
    except (json.JSONDecodeError, OSError):
        return {}


def _save_registry(
    registry: dict[str, dict[str, Any]],
    registry_path: Path | None = None,
) -> None:
    """Persist registry atomically (write to .tmp then rename)."""
    rp = _resolve_registry_path(registry_path)
    rp.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = rp.with_suffix(rp.suffix + ".tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(registry, f, indent=2, sort_keys=True)
    os.replace(tmp_path, rp)


def register(
    path: str | os.PathLike[str],
    registry_path: Path | None = None,
) -> dict[str, Any]:
    """Register a file. Re-registering updates the recorded triplet."""
    p = Path(path).resolve()
    _validate_target(p)
    registry = _load_registry(registry_path)
    entry = _stat_triplet(p)
    registry[str(p)] = entry
    _save_registry(registry, registry_path)
    return entry


def unregister(
    path: str | os.PathLike[str],
    registry_path: Path | None = None,
) -> bool:
    """Remove a file from the registry. Returns True if it was present."""
    p = Path(path).resolve()
    registry = _load_registry(registry_path)
    key = str(p)
    if key not in registry:
        return False
    del registry[key]
    _save_registry(registry, registry_path)
    return True


def refresh(
    path: str | os.PathLike[str],
    registry_path: Path | None = None,
) -> dict[str, Any]:
    """Update stored triplet after legitimate change. Raises if not registered."""
    p = Path(path).resolve()
    _validate_target(p)
    registry = _load_registry(registry_path)
    key = str(p)
    if key not in registry:
        raise KeyError(f"not registered: {key}")
    entry = _stat_triplet(p)
    registry[key] = entry
    _save_registry(registry, registry_path)
    return entry


def _check_entry(key: str, expected: dict[str, Any]) -> dict[str, Any] | None:
    """Verify one entry. Returns failure dict or None on success."""
    p = Path(key)
    if not p.exists():
        return {"path": key, "reason": "missing", "expected": expected}
    if not p.is_file():
        return {"path": key, "reason": "not_a_file", "expected": expected}
    try:
        actual = _stat_triplet(p)
    except OSError as e:
        return {"path": key, "reason": f"stat_error: {e}", "expected": expected}
    if actual["sha256"] != expected.get("sha256"):
        return {
            "path": key,
            "reason": "sha256_mismatch",
            "expected": expected,
            "actual": actual,
        }
    return None


def verify_all(
    registry_path: Path | None = None,
) -> list[dict[str, Any]]:
    """Verify all registered files. Returns list of failures (empty = pass)."""
    registry = _load_registry(registry_path)
    failures: list[dict[str, Any]] = []
    for key, expected in registry.items():
        failure = _check_entry(key, expected)
        if failure is not None:
            failures.append(failure)
    return failures


def list_registered(
    registry_path: Path | None = None,
) -> list[dict[str, Any]]:
    """List all registered entries (path + stored triplet)."""
    registry = _load_registry(registry_path)
    return [{"path": k, **v} for k, v in sorted(registry.items())]
