#!/usr/bin/env python3
"""
GRIP Mesh Discovery

Manages peer registry and consent rules for the GRIP mesh network.
Peers are stored in ~/.claude/mesh-peers.yaml; consent rules in
~/.claude/mesh-consent.yaml.

Part of GRIP Mesh Phase 2.
"""

from datetime import date
from pathlib import Path
from typing import Any

import yaml

CLAUDE_DIR = Path.home() / ".claude"
PEERS_FILE = CLAUDE_DIR / "mesh-peers.yaml"
CONSENT_FILE = CLAUDE_DIR / "mesh-consent.yaml"

_DEFAULT_PEERS: dict[str, Any] = {"version": 1, "peers": []}

_DEFAULT_CONSENT: dict[str, Any] = {
    "version": 1,
    "consent": {
        "shared_types": [],
        "max_results_per_query": 10,
        "share_genes": False,
        "max_content_length": 500,
        "rate_limit": 30,
    },
}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _read_yaml(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    """Return parsed YAML from *path*, or a deep copy of *default* when the file is absent."""
    import copy
    if not path.exists():
        return copy.deepcopy(default)
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    return data if isinstance(data, dict) else copy.deepcopy(default)


def _write_yaml(path: Path, data: dict[str, Any]) -> None:
    """Atomically write *data* to *path* using safe_dump."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".yaml.tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(data, fh, default_flow_style=False, allow_unicode=True)
    tmp.replace(path)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def load_peers() -> list[dict[str, Any]]:
    """Return the list of registered mesh peers."""
    data = _read_yaml(PEERS_FILE, _DEFAULT_PEERS)
    return data.get("peers") or []


def add_peer(name: str, url: str, trust: str = "tailscale") -> None:
    """Add or replace a peer entry in the registry."""
    data = _read_yaml(PEERS_FILE, _DEFAULT_PEERS)
    peers: list[dict[str, Any]] = data.get("peers") or []
    peers = [p for p in peers if p.get("name") != name]
    peers.append({"name": name, "url": url, "trust": trust, "added": str(date.today())})
    data["peers"] = peers
    _write_yaml(PEERS_FILE, data)


def remove_peer(name: str) -> bool:
    """Remove a peer by name. Returns True if a peer was removed."""
    data = _read_yaml(PEERS_FILE, _DEFAULT_PEERS)
    peers: list[dict[str, Any]] = data.get("peers") or []
    filtered = [p for p in peers if p.get("name") != name]
    if len(filtered) == len(peers):
        return False
    data["peers"] = filtered
    _write_yaml(PEERS_FILE, data)
    return True


def load_consent() -> dict[str, Any]:
    """Return the consent configuration block."""
    data = _read_yaml(CONSENT_FILE, _DEFAULT_CONSENT)
    return data.get("consent") or _DEFAULT_CONSENT["consent"]


def filter_results(results: list[dict[str, Any]], consent: dict[str, Any]) -> list[dict[str, Any]]:
    """Apply consent rules to *results* before sharing with a mesh peer."""
    shared_types: list[str] = consent.get("shared_types") or []
    max_results: int = consent.get("max_results_per_query") or 10
    max_len: int = consent.get("max_content_length") or 500
    share_genes: bool = bool(consent.get("share_genes", False))

    filtered = []
    for item in results:
        if shared_types and item.get("type") not in shared_types:
            continue
        if not share_genes and item.get("type") == "gene":
            continue
        entry = dict(item)
        if "content" in entry and isinstance(entry["content"], str):
            entry["content"] = entry["content"][:max_len]
        filtered.append(entry)
        if len(filtered) >= max_results:
            break

    return filtered
