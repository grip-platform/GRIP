#!/usr/bin/env python3
"""Shared types and utilities for org repository graph analysis.

Extracted from org_repo_graph.py to break the import cycle with
ast_repo_analyzer.py (DIP violation — SOLID audit item, W2 fix).

Both org_repo_graph and ast_repo_analyzer import from this module
instead of from each other.
"""

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class RepoEdge:
    """A dependency between two repos."""

    source: str
    target: str
    edge_type: str  # api_call | shared_db | docker_compose |
    #                  shared_package | github_actions | config_ref
    evidence: list[str] = field(default_factory=list)
    strength: str = "moderate"  # strong | moderate | weak
    direction: str = "outbound"  # outbound | inbound | bidirectional
    description: str = ""


def _is_excluded(path: Path) -> bool:
    """Skip dependency and build folders."""
    excluded = {"node_modules", ".next", "dist", "build", "__pycache__",
                "venv", ".venv", "target", "vendor", "Pods", ".git"}
    return any(p in excluded for p in path.parts)


def _repo_name_variants(repo_name: str) -> list[str]:
    """Generate name variants for matching (e.g. acme_api -> acme-api, acmeapi)."""
    variants = [repo_name]
    if "_" in repo_name:
        variants.append(repo_name.replace("_", "-"))
        variants.append(repo_name.replace("_", ""))
    if "-" in repo_name:
        variants.append(repo_name.replace("-", "_"))
        variants.append(repo_name.replace("-", ""))
    return list(set(variants))
