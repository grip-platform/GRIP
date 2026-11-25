"""Gene Recommender -- suggest optimal gene combinations for task types.

Analyses convergence history to recommend which genes work best for
specific task types.  Handles cold-start (new task types) via transfer
learning from the most similar known type.

CLI:
    python -m lib.gene_recommender recommend <task_type>
    python -m lib.gene_recommender cold-start <new_type>
    python -m lib.gene_recommender transfer <source> <target>
    python -m lib.gene_recommender coverage
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class Recommendation:
    """A gene recommendation for a particular task type."""

    task_type: str
    recommended_genes: list[str]
    confidence: float  # 0-1
    reasoning: str
    source: str  # "historical" | "transfer" | "default"

    def __post_init__(self) -> None:
        if not 0 <= self.confidence <= 1:
            raise ValueError(f"confidence must be 0-1, got {self.confidence}")
        if self.source not in ("historical", "transfer", "default"):
            raise ValueError(f"source must be historical/transfer/default, got {self.source}")


@dataclass
class TaskTypeSimilarity:
    """Similarity metric between two task types based on gene overlap."""

    type_a: str
    type_b: str
    similarity: float  # 0-1
    shared_genes: list[str]

    def __post_init__(self) -> None:
        if not 0 <= self.similarity <= 1:
            raise ValueError(f"similarity must be 0-1, got {self.similarity}")


@dataclass
class CoverageEntry:
    """Coverage data for a single task type."""

    task_type: str
    sample_count: int
    success_rate: float
    unique_genes: int
    top_genes: list[str]
    strength: str  # "strong" | "moderate" | "weak"


# ---------------------------------------------------------------------------
# Core functions
# ---------------------------------------------------------------------------

def _extract_successful_genes(history: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Build per-task-type gene success statistics from history.

    Returns ``{task_type: {"gene_wins": {gene: count}, "gene_total": {gene: count},
    "total": int, "satisfied": int}}``
    """
    stats: dict[str, dict[str, Any]] = {}
    for entry in history:
        tt = entry.get("task_type", "unknown")
        if tt not in stats:
            stats[tt] = {"gene_wins": {}, "gene_total": {}, "total": 0, "satisfied": 0}
        stats[tt]["total"] += 1
        genes = entry.get("invoked_genes", [])
        satisfied = entry.get("satisfied", False)
        if satisfied:
            stats[tt]["satisfied"] += 1
        for g in genes:
            stats[tt]["gene_total"].setdefault(g, 0)
            stats[tt]["gene_total"][g] += 1
            if satisfied:
                stats[tt]["gene_wins"].setdefault(g, 0)
                stats[tt]["gene_wins"][g] += 1
    return stats


def _rank_genes(gene_wins: dict[str, int], gene_total: dict[str, int], max_genes: int) -> list[str]:
    """Rank genes by win-rate then by total usage (tie-break)."""
    scored: list[tuple[str, float, int]] = []
    for gene, wins in gene_wins.items():
        total = gene_total.get(gene, 1)
        rate = wins / total
        scored.append((gene, rate, wins))
    # Sort by rate desc, then by absolute wins desc, then by name asc for stability
    scored.sort(key=lambda x: (-x[1], -x[2], x[0]))
    return [g for g, _, _ in scored[:max_genes]]


def recommend_genes(
    task_type: str,
    history: list[dict[str, Any]],
    max_genes: int = 5,
) -> Recommendation:
    """Recommend optimal genes for *task_type* from convergence *history*.

    If the task type has no history, falls back to :func:`cold_start_recommend`
    using all known types.  If there is no history at all, returns a default
    recommendation.
    """
    if max_genes < 1:
        raise ValueError("max_genes must be >= 1")

    stats = _extract_successful_genes(history)

    if task_type in stats and stats[task_type]["satisfied"] > 0:
        s = stats[task_type]
        genes = _rank_genes(s["gene_wins"], s["gene_total"], max_genes)
        total = s["total"]
        satisfied = s["satisfied"]
        confidence = min(1.0, satisfied / total * min(1.0, total / 5))
        return Recommendation(
            task_type=task_type,
            recommended_genes=genes,
            confidence=round(confidence, 4),
            reasoning=f"Based on {satisfied}/{total} successful runs",
            source="historical",
        )

    # Cold-start path
    known_types = list(stats.keys())
    if known_types:
        return cold_start_recommend(task_type, history, known_types, max_genes=max_genes)

    # No history at all -- default
    return Recommendation(
        task_type=task_type,
        recommended_genes=[],
        confidence=0.0,
        reasoning="No history available",
        source="default",
    )


def cold_start_recommend(
    new_type: str,
    history: list[dict[str, Any]],
    known_types: list[str],
    max_genes: int = 5,
) -> Recommendation:
    """Handle a new (unseen) task type via transfer from the most similar known type."""
    if not known_types:
        return Recommendation(
            task_type=new_type,
            recommended_genes=[],
            confidence=0.0,
            reasoning="No known types to transfer from",
            source="default",
        )

    best_sim: TaskTypeSimilarity | None = None
    for kt in known_types:
        sim = compute_type_similarity(kt, new_type, history)
        if best_sim is None or sim.similarity > best_sim.similarity:
            best_sim = sim

    if best_sim is None or best_sim.similarity == 0:
        # Fallback: use the type with the most successful data
        stats = _extract_successful_genes(history)
        best_type = max(known_types, key=lambda t: stats.get(t, {}).get("satisfied", 0))
        rec = recommend_genes(best_type, history, max_genes=max_genes)
        return Recommendation(
            task_type=new_type,
            recommended_genes=rec.recommended_genes,
            confidence=round(rec.confidence * 0.3, 4),
            reasoning=f"Fallback transfer from '{best_type}' (no gene overlap found)",
            source="transfer",
        )

    return transfer_genes(best_sim.type_a, new_type, history, max_genes=max_genes)


def compute_type_similarity(
    type_a: str,
    type_b: str,
    history: list[dict[str, Any]],
) -> TaskTypeSimilarity:
    """Compute similarity between two task types by gene overlap in successful runs."""
    stats = _extract_successful_genes(history)

    genes_a = set(stats.get(type_a, {}).get("gene_wins", {}).keys())
    genes_b = set(stats.get(type_b, {}).get("gene_wins", {}).keys())

    shared = sorted(genes_a & genes_b)
    union = genes_a | genes_b

    if not union:
        return TaskTypeSimilarity(type_a=type_a, type_b=type_b, similarity=0.0, shared_genes=[])

    similarity = len(shared) / len(union)  # Jaccard index
    return TaskTypeSimilarity(
        type_a=type_a,
        type_b=type_b,
        similarity=round(similarity, 4),
        shared_genes=shared,
    )


def transfer_genes(
    source_type: str,
    target_type: str,
    history: list[dict[str, Any]],
    max_genes: int = 5,
) -> Recommendation:
    """Transfer gene recommendations from *source_type* to *target_type* with reduced confidence."""
    rec = recommend_genes(source_type, history, max_genes=max_genes)
    discount = 0.6  # transfer discount factor
    return Recommendation(
        task_type=target_type,
        recommended_genes=rec.recommended_genes,
        confidence=round(rec.confidence * discount, 4),
        reasoning=f"Transferred from '{source_type}' ({rec.reasoning})",
        source="transfer",
    )


def coverage_report(history: list[dict[str, Any]]) -> list[CoverageEntry]:
    """Which task types have strong vs weak gene data."""
    stats = _extract_successful_genes(history)
    entries: list[CoverageEntry] = []
    for tt, s in sorted(stats.items()):
        total = s["total"]
        satisfied = s["satisfied"]
        success_rate = satisfied / total if total else 0
        unique = len(s["gene_wins"])
        top = _rank_genes(s["gene_wins"], s["gene_total"], 3)
        if total >= 10 and success_rate >= 0.6:
            strength = "strong"
        elif total >= 3:
            strength = "moderate"
        else:
            strength = "weak"
        entries.append(CoverageEntry(
            task_type=tt,
            sample_count=total,
            success_rate=round(success_rate, 4),
            unique_genes=unique,
            top_genes=top,
            strength=strength,
        ))
    return entries


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

def format_recommendation(rec: Recommendation) -> str:
    """Human-readable recommendation string."""
    genes_str = ", ".join(rec.recommended_genes) if rec.recommended_genes else "(none)"
    lines = [
        f"Recommendation for '{rec.task_type}'",
        f"  Genes: {genes_str}",
        f"  Confidence: {rec.confidence:.0%}",
        f"  Source: {rec.source}",
        f"  Reasoning: {rec.reasoning}",
    ]
    return "\n".join(lines)


def format_coverage(coverage: list[CoverageEntry]) -> str:
    """ASCII table of coverage data."""
    if not coverage:
        return "No coverage data."
    # Column widths
    type_w = max(len(e.task_type) for e in coverage)
    type_w = max(type_w, 9)  # min header width
    header = (
        f"{'Task Type':<{type_w}}  "
        f"{'Samples':>7}  "
        f"{'Success':>7}  "
        f"{'Genes':>5}  "
        f"{'Strength':<8}  "
        f"Top Genes"
    )
    sep = "-" * len(header)
    rows = [header, sep]
    for e in coverage:
        top = ", ".join(e.top_genes) if e.top_genes else "-"
        rows.append(
            f"{e.task_type:<{type_w}}  "
            f"{e.sample_count:>7}  "
            f"{e.success_rate:>7.0%}  "
            f"{e.unique_genes:>5}  "
            f"{e.strength:<8}  "
            f"{top}"
        )
    return "\n".join(rows)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _load_history(path: str | None = None) -> list[dict[str, Any]]:
    """Load history from a JSON file or return empty list."""
    if path is None:
        candidates = [
            Path.home() / ".claude" / "data" / "convergence-history.json",
            Path.home() / ".claude" / "cache" / "convergence-history.json",
        ]
        for c in candidates:
            if c.exists():
                return json.loads(c.read_text())
        return []
    p = Path(path)
    if not p.exists():
        return []
    return json.loads(p.read_text())


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    args = argv if argv is not None else sys.argv[1:]
    if not args:
        print("Usage: gene_recommender <command> [args]")
        print("Commands: recommend <type>, cold-start <type>, transfer <src> <tgt>, coverage")
        return 1

    cmd = args[0]
    # Optional --history flag
    history_path: str | None = None
    remaining = args[1:]
    if "--history" in remaining:
        idx = remaining.index("--history")
        if idx + 1 < len(remaining):
            history_path = remaining[idx + 1]
            remaining = remaining[:idx] + remaining[idx + 2 :]
        else:
            print("Error: --history requires a path")
            return 1

    history = _load_history(history_path)

    if cmd == "recommend":
        if not remaining:
            print("Error: recommend requires a task type")
            return 1
        rec = recommend_genes(remaining[0], history)
        print(format_recommendation(rec))
        return 0

    if cmd == "cold-start":
        if not remaining:
            print("Error: cold-start requires a task type")
            return 1
        stats = _extract_successful_genes(history)
        known = list(stats.keys())
        rec = cold_start_recommend(remaining[0], history, known)
        print(format_recommendation(rec))
        return 0

    if cmd == "transfer":
        if len(remaining) < 2:
            print("Error: transfer requires <source> <target>")
            return 1
        rec = transfer_genes(remaining[0], remaining[1], history)
        print(format_recommendation(rec))
        return 0

    if cmd == "coverage":
        cov = coverage_report(history)
        print(format_coverage(cov))
        return 0

    print(f"Unknown command: {cmd}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
