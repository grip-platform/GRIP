#!/usr/bin/env python3
"""
mesh_gene_exchange.py — Cross-instance genome breeding via the GRIP mesh protocol.

When a mesh query returns results, the responding instance can optionally attach
convergence strategies (genes) that proved effective for similar problems. The
receiving instance imports them with a trust discount, preventing blind adoption
of external claims.

Consent gate: share_genes in mesh-consent.yaml MUST be True before any genes
are offered. This is a hard gate — not advisory.

Usage (offer side):
    strategies = find_transferable(target_domains=["DRY"])
    genes = find_genes_for_query("DRY deduplication in Python")
    # genes goes into MeshResponse.offered_genes

Usage (receive side):
    count = receive_offered_genes(response.offered_genes, trust_level=0.5)
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

_CLAUDE_DIR = Path.home() / ".claude"
if str(Path(__file__).resolve().parent) not in sys.path:  # own lib (GRIP#9857)
    sys.path.insert(0, str(Path(__file__).resolve().parent))

import converge_transfer
import mesh_discovery

# The default consent file location, re-exported so callers can patch it.
CONSENT_FILE = mesh_discovery.CONSENT_FILE

# Fields kept on the wire — no internal-only fields leave this machine.
_WIRE_FIELDS = {"domain", "strategy_name", "score_delta", "confidence", "content_hash"}

_DEFAULT_CONSENT: dict[str, Any] = {
    "shared_types": [],
    "max_results_per_query": 10,
    "share_genes": False,
    "max_content_length": 500,
    "rate_limit": 30,
}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _load_consent_from(consent_path: Path) -> dict[str, Any]:
    """Read the consent block from *consent_path*, returning defaults when absent."""
    import copy
    if not consent_path.exists():
        return copy.deepcopy(_DEFAULT_CONSENT)
    try:
        import yaml
        data = yaml.safe_load(consent_path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data.get("consent", copy.deepcopy(_DEFAULT_CONSENT))
    except Exception:
        pass
    return copy.deepcopy(_DEFAULT_CONSENT)


def _keyword_match(query: str, strategy: converge_transfer.ConvergenceStrategy) -> bool:
    """Return True when any query keyword appears in domain or strategy_name."""
    tokens = {t.lower() for t in query.split() if len(t) > 2}
    haystack = f"{strategy.domain} {strategy.strategy_name}".lower()
    return any(tok in haystack for tok in tokens)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def find_genes_for_query(
    query_text: str,
    max_genes: int = 3,
    consent_file: Path = CONSENT_FILE,
) -> list[dict[str, Any]]:
    """Find convergence strategies relevant to a mesh query.

    Uses keyword matching against strategy domain and strategy_name fields.
    Returns a list of wire-safe gene dicts for MeshResponse.offered_genes.

    Returns an empty list when share_genes is False in the consent config.
    Consent is always checked first, regardless of local strategy availability.

    Args:
        query_text:   Free-text query from the mesh peer.
        max_genes:    Maximum number of genes to return.
        consent_file: Path to mesh-consent.yaml (injectable for tests).
    """
    consent = _load_consent_from(consent_file)
    if not consent.get("share_genes", False):
        return []

    candidates = converge_transfer.find_transferable(limit=50)
    matched = [s for s in candidates if _keyword_match(query_text, s)]
    top = matched[:max_genes] if matched else candidates[:max_genes]
    return prepare_gene_offer(top)


def receive_offered_genes(
    genes: list[dict[str, Any]],
    trust_level: float = 0.5,
) -> int:
    """Import offered genes from a mesh peer into the local strategy store.

    Each gene's confidence is multiplied by trust_level before storage, so
    external claims are never adopted at face value.

    Args:
        genes:       List of wire-safe gene dicts from a peer's offered_genes.
        trust_level: Confidence multiplier in [0.0, 1.0]. Default 0.5.

    Returns:
        Count of genes actually imported (skips duplicates already in store).
    """
    if not genes:
        return 0

    trust_level = max(0.0, min(1.0, trust_level))
    strategies = _genes_to_strategy_dicts(genes)

    package = converge_transfer.StrategyPackage(
        name="mesh-peer-offer",
        author="mesh-peer",
        strategies=strategies,
        description="Genes received from a mesh peer",
    )
    result = converge_transfer.import_package(package, trust_level=trust_level)
    return result.get("imported", 0)


def prepare_gene_offer(
    strategies: list[converge_transfer.ConvergenceStrategy],
) -> list[dict[str, Any]]:
    """Convert ConvergenceStrategy objects to wire-safe dicts for mesh transport.

    Strips internal fields; retains only: domain, strategy_name, score_delta,
    confidence, and content_hash (for deduplication on the receiver side).

    Args:
        strategies: List of ConvergenceStrategy objects to serialise.

    Returns:
        List of dicts safe to embed in a MeshResponse.offered_genes payload.
    """
    result = []
    for s in strategies:
        result.append({
            "domain": s.domain,
            "strategy_name": s.strategy_name,
            "score_delta": round(s.improvement, 3),
            "confidence": round(s.confidence, 3),
            "content_hash": s.content_hash,
        })
    return result


# ---------------------------------------------------------------------------
# Internal conversion helper
# ---------------------------------------------------------------------------


def _genes_to_strategy_dicts(genes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Expand wire-format gene dicts to the fuller format import_package expects."""
    out = []
    for g in genes:
        delta = g.get("score_delta", 0.0)
        out.append({
            "domain": g.get("domain", "unknown"),
            "strategy_name": g.get("strategy_name", "unknown"),
            "description": f"Mesh-received gene: {g.get('strategy_name', '?')}",
            "score_before": 0.0,
            "score_after": delta,
            "improvement": delta,
            "confidence": g.get("confidence", 0.0),
            "times_applied": 1,
            "times_succeeded": 1,
            "tags": ["mesh-received"],
            "content_hash": g.get("content_hash", ""),
        })
    return out
