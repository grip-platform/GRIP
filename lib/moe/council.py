"""
MoE Multi-Provider Council
===========================

Council composition for multi-model deliberation with weighted voting
and Kolmogorov-Smirnov divergence detection.

Each provider scores independently. The council merges scores via
weighted median. Divergent providers (KS statistic > threshold) are
flagged for human review.

Usage:
    from lib.moe.council import MoECouncil
    from lib.moe.provider_registry import ScoreResult

    council = MoECouncil()
    merged = council.deliberate([
        ScoreResult(provider="llama", composite_score=85, dimension_scores={...}),
        ScoreResult(provider="haiku", composite_score=82, dimension_scores={...}),
    ], weights={"llama": 0.4, "haiku": 0.6})
"""

from dataclasses import dataclass, field
from typing import Optional


# KS divergence threshold: providers with statistic > this are flagged
KS_DIVERGENCE_THRESHOLD = 0.3


@dataclass
class CouncilVerdict:
    """Merged result from multi-provider council deliberation."""
    composite_score: int = 0
    dimension_scores: dict = field(default_factory=dict)
    passed: bool = False
    provider_scores: dict = field(default_factory=dict)  # provider -> composite
    divergent_providers: list = field(default_factory=list)
    ks_statistics: dict = field(default_factory=dict)  # pair -> KS stat
    num_providers: int = 0
    suggestions: list = field(default_factory=list)

    @property
    def has_divergence(self) -> bool:
        return len(self.divergent_providers) > 0


def weighted_median(values: list[float], weights: list[float]) -> float:
    """Compute weighted median of values.

    The weighted median is the value where the cumulative weight
    crosses 50% of total weight. More robust than weighted mean
    against outlier scores from noisy providers.
    """
    if not values:
        return 0.0
    if len(values) == 1:
        return values[0]

    # Sort by value, keeping weights paired
    paired = sorted(zip(values, weights), key=lambda x: x[0])
    total_weight = sum(w for _, w in paired)
    if total_weight == 0:
        return sum(values) / len(values)

    cumulative = 0.0
    for val, weight in paired:
        cumulative += weight
        if cumulative >= total_weight / 2:
            return val
    return paired[-1][0]


def ks_statistic(scores_a: list[int], scores_b: list[int]) -> float:
    """Compute Kolmogorov-Smirnov statistic between two score distributions.

    KS measures the maximum difference between two empirical CDFs.
    Returns a value in [0, 1]: 0 = identical distributions,
    1 = completely different.

    For PR review scores, we compare the dimension score vectors
    from two providers (4-5 dimensions each).
    """
    if not scores_a or not scores_b:
        return 0.0

    # Combine and sort all unique values
    all_values = sorted(set(scores_a + scores_b))
    if not all_values:
        return 0.0

    max_diff = 0.0
    for val in all_values:
        # Empirical CDF for each distribution
        cdf_a = sum(1 for s in scores_a if s <= val) / len(scores_a)
        cdf_b = sum(1 for s in scores_b if s <= val) / len(scores_b)
        diff = abs(cdf_a - cdf_b)
        if diff > max_diff:
            max_diff = diff

    return max_diff


class MoECouncil:
    """Multi-provider council for PR review deliberation."""

    def __init__(self, ks_threshold: float = KS_DIVERGENCE_THRESHOLD):
        self.ks_threshold = ks_threshold

    def deliberate(self, results: list, weights: Optional[dict] = None) -> CouncilVerdict:
        """Merge scores from multiple providers via weighted median.

        Args:
            results: List of ScoreResult from different providers
            weights: Optional dict mapping provider name -> weight (0-1).
                     If not provided, all providers weighted equally.

        Returns:
            CouncilVerdict with merged scores and divergence analysis
        """
        # Filter out errored results
        valid = [r for r in results if not getattr(r, "error", "")]
        if not valid:
            return CouncilVerdict()

        if len(valid) == 1:
            r = valid[0]
            return CouncilVerdict(
                composite_score=r.composite_score,
                dimension_scores=r.dimension_scores,
                passed=r.composite_score >= 80,
                provider_scores={r.provider: r.composite_score},
                num_providers=1,
                suggestions=getattr(r, "suggestions", []),
            )

        # Default weights: equal
        if weights is None:
            weights = {r.provider: 1.0 for r in valid}

        # Merge composite scores via weighted median
        composites = [r.composite_score for r in valid]
        w_list = [weights.get(r.provider, 1.0) for r in valid]
        merged_composite = int(weighted_median(composites, w_list))

        # Merge dimension scores via weighted median per dimension
        all_dims = set()
        for r in valid:
            if r.dimension_scores:
                all_dims.update(r.dimension_scores.keys())

        merged_dims = {}
        for dim in all_dims:
            dim_vals = []
            dim_weights = []
            for r in valid:
                if r.dimension_scores and dim in r.dimension_scores:
                    dim_vals.append(r.dimension_scores[dim])
                    dim_weights.append(weights.get(r.provider, 1.0))
            if dim_vals:
                merged_dims[dim] = int(weighted_median(dim_vals, dim_weights))

        # KS divergence detection between all pairs
        ks_stats = {}
        divergent = []
        provider_names = [r.provider for r in valid]
        for i in range(len(valid)):
            for j in range(i + 1, len(valid)):
                scores_i = self._extract_score_vector(valid[i])
                scores_j = self._extract_score_vector(valid[j])
                ks = ks_statistic(scores_i, scores_j)
                pair_key = f"{provider_names[i]}:{provider_names[j]}"
                ks_stats[pair_key] = round(ks, 3)
                if ks > self.ks_threshold:
                    divergent.append(pair_key)

        # Merge suggestions from all providers
        all_suggestions = []
        for r in valid:
            for s in getattr(r, "suggestions", []):
                if s not in all_suggestions:
                    all_suggestions.append(s)

        return CouncilVerdict(
            composite_score=merged_composite,
            dimension_scores=merged_dims,
            passed=merged_composite >= 80,
            provider_scores={r.provider: r.composite_score for r in valid},
            divergent_providers=divergent,
            ks_statistics=ks_stats,
            num_providers=len(valid),
            suggestions=all_suggestions,
        )

    def _extract_score_vector(self, result) -> list[int]:
        """Extract dimension scores as a list for KS comparison."""
        if not result.dimension_scores:
            return [result.composite_score]
        return list(result.dimension_scores.values())
