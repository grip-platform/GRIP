#!/usr/bin/env python3
"""Gene interaction predictor -- predict gene synergies from historical co-expression data.

Analyzes JSONL run history to compute pairwise gene co-occurrence statistics,
synergy scores, optimal gene set selection, and anti-pattern detection.
"""

import argparse
import json
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from itertools import combinations
from pathlib import Path
from typing import Optional


@dataclass
class GenePair:
    """Statistics for a pair of genes that appear together in runs."""

    gene_a: str
    gene_b: str
    co_occurrences: int = 0
    avg_score_together: float = 0.0
    avg_score_apart: float = 0.0
    synergy_score: float = 0.0

    def __post_init__(self):
        # Normalize ordering so (a,b) == (b,a)
        if self.gene_a > self.gene_b:
            self.gene_a, self.gene_b = self.gene_b, self.gene_a


@dataclass
class Prediction:
    """A prediction about gene combination performance."""

    genes: list
    predicted_score: float = 0.0
    confidence: float = 0.0
    reasoning: str = ""


def _avg_run_score(run: dict) -> float:
    """Compute average dimension score for a single run."""
    scores = run.get("final_dimension_scores", {})
    if not scores:
        return 0.0
    return sum(scores.values()) / len(scores)


def build_pair_stats(runs: list[dict]) -> dict[tuple[str, str], GenePair]:
    """Compute pairwise co-occurrence statistics from run data.

    Args:
        runs: List of run dicts with invoked_genes and final_dimension_scores.

    Returns:
        Dict mapping (gene_a, gene_b) tuples to GenePair stats.
    """
    # Accumulate raw data
    together_scores: dict[tuple[str, str], list[float]] = defaultdict(list)
    apart_scores: dict[tuple[str, str], list[float]] = defaultdict(list)
    all_genes: set[str] = set()

    for run in runs:
        genes = run.get("invoked_genes", [])
        if not genes:
            continue
        avg = _avg_run_score(run)
        gene_set = set(genes)
        all_genes.update(gene_set)

        # Record co-occurrences
        for a, b in combinations(sorted(gene_set), 2):
            together_scores[(a, b)].append(avg)

    # For apart scores: runs where one gene appears without the other
    gene_runs: dict[str, list[float]] = defaultdict(list)
    gene_run_sets: dict[str, list[set]] = defaultdict(list)

    for run in runs:
        genes = run.get("invoked_genes", [])
        if not genes:
            continue
        avg = _avg_run_score(run)
        gene_set = set(genes)
        for g in gene_set:
            gene_runs[g].append(avg)
            gene_run_sets[g].append(gene_set)

    # Build pair stats
    pair_stats: dict[tuple[str, str], GenePair] = {}

    for (a, b), scores_together in together_scores.items():
        # Apart: runs with gene_a but NOT gene_b, and vice versa
        a_without_b = []
        for i, run_set in enumerate(gene_run_sets.get(a, [])):
            if b not in run_set:
                a_without_b.append(gene_runs[a][i])

        b_without_a = []
        for i, run_set in enumerate(gene_run_sets.get(b, [])):
            if a not in run_set:
                b_without_a.append(gene_runs[b][i])

        apart = a_without_b + b_without_a
        avg_together = sum(scores_together) / len(scores_together) if scores_together else 0.0
        avg_apart = sum(apart) / len(apart) if apart else 0.0

        pair = GenePair(
            gene_a=a,
            gene_b=b,
            co_occurrences=len(scores_together),
            avg_score_together=avg_together,
            avg_score_apart=avg_apart,
        )
        pair_stats[(a, b)] = pair

    # Compute synergy after building all pairs
    compute_synergy(pair_stats)

    return pair_stats


def compute_synergy(pair_stats: dict[tuple[str, str], GenePair]) -> None:
    """Compute synergy scores for all gene pairs in-place.

    synergy = avg_score_together - avg_score_apart
    Positive synergy means the genes perform better together.
    """
    for pair in pair_stats.values():
        pair.synergy_score = pair.avg_score_together - pair.avg_score_apart


def predict_synergy(
    gene_a: str, gene_b: str, pair_stats: dict[tuple[str, str], GenePair]
) -> Prediction:
    """Predict whether two genes work well together.

    Args:
        gene_a: First gene name.
        gene_b: Second gene name.
        pair_stats: Pre-computed pair statistics.

    Returns:
        Prediction with predicted score, confidence, and reasoning.
    """
    key = tuple(sorted([gene_a, gene_b]))
    pair = pair_stats.get(key)

    if pair is None:
        return Prediction(
            genes=[gene_a, gene_b],
            predicted_score=0.0,
            confidence=0.0,
            reasoning=f"No co-occurrence data for {gene_a} + {gene_b}",
        )

    # Confidence based on sample size (log scale, cap at 1.0)
    import math

    confidence = min(1.0, math.log2(pair.co_occurrences + 1) / 5.0)

    if pair.synergy_score > 5:
        label = "strongly synergistic"
    elif pair.synergy_score > 0:
        label = "mildly synergistic"
    elif pair.synergy_score > -5:
        label = "neutral"
    else:
        label = "anti-synergistic"

    reasoning = (
        f"{gene_a} + {gene_b}: {label} "
        f"(together={pair.avg_score_together:.1f}, "
        f"apart={pair.avg_score_apart:.1f}, "
        f"synergy={pair.synergy_score:+.1f}, "
        f"n={pair.co_occurrences})"
    )

    return Prediction(
        genes=[gene_a, gene_b],
        predicted_score=pair.avg_score_together,
        confidence=confidence,
        reasoning=reasoning,
    )


def find_synergies(
    gene: str, pair_stats: dict[tuple[str, str], GenePair], top_k: int = 5
) -> list[GenePair]:
    """Find top synergistic partners for a gene.

    Args:
        gene: The gene to find partners for.
        pair_stats: Pre-computed pair statistics.
        top_k: Number of top partners to return.

    Returns:
        List of GenePair sorted by synergy_score descending.
    """
    relevant = []
    for key, pair in pair_stats.items():
        if gene in key:
            relevant.append(pair)

    relevant.sort(key=lambda p: p.synergy_score, reverse=True)
    return relevant[:top_k]


def optimal_gene_set(
    task_type: str,
    pair_stats: dict[tuple[str, str], GenePair],
    max_genes: int = 5,
    runs: Optional[list[dict]] = None,
) -> Prediction:
    """Greedy selection of best gene combination for a task type.

    If runs are provided, filters to runs matching task_type first and
    rebuilds pair_stats from that subset. Otherwise uses provided pair_stats.

    Uses greedy algorithm:
    1. Start with the gene that has the highest average score
    2. Add genes that maximize pairwise synergy with the current set
    3. Stop at max_genes

    Args:
        task_type: The type of task to optimize for.
        pair_stats: Pre-computed pair statistics.
        max_genes: Maximum genes in the set.
        runs: Optional raw run data for task-type filtering.

    Returns:
        Prediction with the optimal gene set.
    """
    # If runs provided, filter by task_type and rebuild stats
    if runs is not None:
        filtered = [r for r in runs if r.get("task_type") == task_type]
        pair_stats = build_pair_stats(filtered)

    if not pair_stats:
        return Prediction(
            genes=[],
            predicted_score=0.0,
            confidence=0.0,
            reasoning=f"No data for task_type={task_type}",
        )

    # Collect all genes and their average scores across pairs
    gene_scores: dict[str, list[float]] = defaultdict(list)
    for (a, b), pair in pair_stats.items():
        gene_scores[a].append(pair.avg_score_together)
        gene_scores[b].append(pair.avg_score_together)

    gene_avg = {g: sum(s) / len(s) for g, s in gene_scores.items() if s}

    if not gene_avg:
        return Prediction(
            genes=[],
            predicted_score=0.0,
            confidence=0.0,
            reasoning=f"No gene scores available for task_type={task_type}",
        )

    # Greedy: start with best gene
    selected = []
    best_gene = max(gene_avg, key=gene_avg.get)
    selected.append(best_gene)

    candidates = set(gene_avg.keys()) - {best_gene}

    while len(selected) < max_genes and candidates:
        best_next = None
        best_synergy_sum = float("-inf")

        for candidate in candidates:
            synergy_sum = 0.0
            for existing in selected:
                key = tuple(sorted([candidate, existing]))
                pair = pair_stats.get(key)
                if pair:
                    synergy_sum += pair.synergy_score
            if synergy_sum > best_synergy_sum:
                best_synergy_sum = synergy_sum
                best_next = candidate

        if best_next is None or best_synergy_sum <= 0:
            break

        selected.append(best_next)
        candidates.discard(best_next)

    # Compute predicted score: average of pairwise together scores
    pair_scores = []
    for a, b in combinations(selected, 2):
        key = tuple(sorted([a, b]))
        pair = pair_stats.get(key)
        if pair:
            pair_scores.append(pair.avg_score_together)

    predicted = sum(pair_scores) / len(pair_scores) if pair_scores else gene_avg.get(selected[0], 0.0)

    # Confidence from total observations
    total_obs = sum(
        pair_stats.get(tuple(sorted([a, b])), GenePair(a, b)).co_occurrences
        for a, b in combinations(selected, 2)
    )
    import math

    confidence = min(1.0, math.log2(total_obs + 1) / 8.0)

    return Prediction(
        genes=selected,
        predicted_score=predicted,
        confidence=confidence,
        reasoning=f"Greedy optimal set for {task_type}: {' + '.join(selected)} (predicted={predicted:.1f})",
    )


def find_anti_patterns(
    pair_stats: dict[tuple[str, str], GenePair], threshold: float = -5.0
) -> list[GenePair]:
    """Find gene combos that correlate with lower scores.

    Args:
        pair_stats: Pre-computed pair statistics.
        threshold: Synergy score threshold (pairs below this are anti-patterns).

    Returns:
        List of GenePair with synergy below threshold, sorted ascending.
    """
    anti = [pair for pair in pair_stats.values() if pair.synergy_score < threshold]
    anti.sort(key=lambda p: p.synergy_score)
    return anti


def format_synergy_report(pairs: list[GenePair]) -> str:
    """Format a human-readable synergy report.

    Args:
        pairs: List of GenePair to report on.

    Returns:
        Formatted multi-line string.
    """
    if not pairs:
        return "No gene pairs to report."

    lines = []
    lines.append(f"{'Gene A':<25} {'Gene B':<25} {'Synergy':>8} {'Together':>10} {'Apart':>10} {'N':>5}")
    lines.append("-" * 88)

    for pair in pairs:
        lines.append(
            f"{pair.gene_a:<25} {pair.gene_b:<25} "
            f"{pair.synergy_score:>+8.1f} "
            f"{pair.avg_score_together:>10.1f} "
            f"{pair.avg_score_apart:>10.1f} "
            f"{pair.co_occurrences:>5}"
        )

    return "\n".join(lines)


def load_runs(path: str) -> list[dict]:
    """Load run data from a JSONL file.

    Args:
        path: Path to JSONL file.

    Returns:
        List of run dicts.
    """
    runs = []
    p = Path(path)
    if not p.exists():
        print(f"File not found: {path}", file=sys.stderr)
        return runs

    with open(p) as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    runs.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return runs


def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Gene interaction predictor -- predict gene synergies from history"
    )
    parser.add_argument(
        "--data",
        default=str(Path.home() / ".claude" / "data" / "gene-runs.jsonl"),
        help="Path to JSONL run data file",
    )

    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # synergy <gene>
    syn_parser = subparsers.add_parser("synergy", help="Find top synergistic partners for a gene")
    syn_parser.add_argument("gene", help="Gene to find partners for")
    syn_parser.add_argument("--top-k", type=int, default=5, help="Number of top partners")

    # optimal <task_type>
    opt_parser = subparsers.add_parser("optimal", help="Find optimal gene set for a task type")
    opt_parser.add_argument("task_type", help="Task type to optimize for")
    opt_parser.add_argument("--max-genes", type=int, default=5, help="Max genes in set")

    # anti-patterns
    anti_parser = subparsers.add_parser("anti-patterns", help="Find anti-synergistic gene pairs")
    anti_parser.add_argument("--threshold", type=float, default=-5.0, help="Synergy threshold")

    # predict <gene1> <gene2>
    pred_parser = subparsers.add_parser("predict", help="Predict synergy between two genes")
    pred_parser.add_argument("gene1", help="First gene")
    pred_parser.add_argument("gene2", help="Second gene")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    runs = load_runs(args.data)
    if not runs:
        print(f"No runs loaded from {args.data}", file=sys.stderr)
        sys.exit(1)

    pair_stats = build_pair_stats(runs)

    if args.command == "synergy":
        pairs = find_synergies(args.gene, pair_stats, top_k=args.top_k)
        if pairs:
            print(f"Top synergistic partners for '{args.gene}':\n")
            print(format_synergy_report(pairs))
        else:
            print(f"No synergy data found for gene '{args.gene}'")

    elif args.command == "optimal":
        prediction = optimal_gene_set(args.task_type, pair_stats, max_genes=args.max_genes, runs=runs)
        print(f"Optimal gene set for '{args.task_type}':\n")
        print(f"  Genes: {', '.join(prediction.genes)}")
        print(f"  Predicted score: {prediction.predicted_score:.1f}")
        print(f"  Confidence: {prediction.confidence:.2f}")
        print(f"  Reasoning: {prediction.reasoning}")

    elif args.command == "anti-patterns":
        anti = find_anti_patterns(pair_stats, threshold=args.threshold)
        if anti:
            print(f"Anti-patterns (synergy < {args.threshold}):\n")
            print(format_synergy_report(anti))
        else:
            print(f"No anti-patterns found (threshold={args.threshold})")

    elif args.command == "predict":
        prediction = predict_synergy(args.gene1, args.gene2, pair_stats)
        print(f"Prediction for {args.gene1} + {args.gene2}:\n")
        print(f"  Predicted score: {prediction.predicted_score:.1f}")
        print(f"  Confidence: {prediction.confidence:.2f}")
        print(f"  Reasoning: {prediction.reasoning}")


if __name__ == "__main__":
    main()
