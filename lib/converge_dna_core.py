"""
converge_dna_core.py — Data structures and constants for the evolutionary DNA layer.

Contains all dataclasses, constants, and the InvocationRegistry.
No dependencies on other converge_dna modules.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import zlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", str(Path.home() / ".claude")))


@dataclass(frozen=True)
class _DNAConfig:
    """Immutable configuration for the converge DNA core.

    Centralises all constants so tests can swap a single object
    without patching scattered module-level names.
    """
    # File paths (derived from CLAUDE_DIR at import time)
    genome_path: Path = field(default_factory=lambda: CLAUDE_DIR / "cache" / "genome.json")
    genome_history_path: Path = field(default_factory=lambda: CLAUDE_DIR / "cache" / "genome-history.jsonl")
    genome_outcomes_path: Path = field(default_factory=lambda: CLAUDE_DIR / "cache" / "genome-outcomes.jsonl")
    review_verdicts_path: Path = field(default_factory=lambda: CLAUDE_DIR / "cache" / "review-verdicts.jsonl")

    # Fitness constants (aligned with evolutionary_refactor.py and kono.py)
    decay_rate: float = 0.95
    protected_floor: float = 0.8
    kono_permanent_floor: float = 0.6
    kono_attestation_bonus: float = 0.05
    neutral_fitness: float = 0.5
    dead_threshold: float = 0.2
    survival_floor: float = 0.3   # Floor for proven genes (invocation_count >= 1)
    nursery_generations: int = 5

    # Review trigger thresholds
    review_improvement_threshold: float = 15.0
    review_regression_threshold: float = -5.0

    # CUBE domains — all 8 design principles (tuple so it's truly immutable)
    # Uses BIG-O (hyphen) to match cube.py DESIGN_DOMAINS and subsystem_bridge keys
    cube_domains: tuple = ("DRY", "KISS", "CC", "BIG-O", "SOLID", "GRASP", "YAGNI", "YSH")

    # Default evaluation policy (frozen via types.MappingProxyType at access time)
    eval_policy: tuple = field(default_factory=lambda: (
        ("invoked_success", 0.05),
        ("fast_convergence_bonus", 0.02),
        ("invoked_failure", -0.02),
        ("expressed_success", 0.01),
        ("expressed_failure", -0.01),
        ("high_improvement_bonus", 0.03),
        ("high_improvement_threshold", 10.0),
        ("negative_improvement_penalty", -0.01),
        ("negative_improvement_threshold", -5.0),
    ))

    def eval_policy_dict(self) -> dict:
        """Return eval policy as a plain dict (for callers that need mutation)."""
        return dict(self.eval_policy)


_CONFIG = _DNAConfig()

# Backward-compatible module-level aliases
GENOME_PATH = _CONFIG.genome_path
GENOME_HISTORY_PATH = _CONFIG.genome_history_path
GENOME_OUTCOMES_PATH = _CONFIG.genome_outcomes_path
REVIEW_VERDICTS_PATH = _CONFIG.review_verdicts_path
DECAY_RATE = _CONFIG.decay_rate
PROTECTED_FLOOR = _CONFIG.protected_floor
KONO_PERMANENT_FLOOR = _CONFIG.kono_permanent_floor
KONO_ATTESTATION_BONUS = _CONFIG.kono_attestation_bonus
NEUTRAL_FITNESS = _CONFIG.neutral_fitness
DEAD_THRESHOLD = _CONFIG.dead_threshold
SURVIVAL_FLOOR = _CONFIG.survival_floor
NURSERY_GENERATIONS = _CONFIG.nursery_generations
CUBE_DOMAINS = list(_CONFIG.cube_domains)  # callers expect a list
DEFAULT_EVAL_POLICY = _CONFIG.eval_policy_dict()  # mutable copy for EvaluationPolicy.__init__
REVIEW_IMPROVEMENT_THRESHOLD = _CONFIG.review_improvement_threshold
REVIEW_REGRESSION_THRESHOLD = _CONFIG.review_regression_threshold


# =========================================================================
# Invocation Registry
# =========================================================================

class InvocationRegistry:
    """Explicit registry for tracking gene invocations during a converge run.

    Replaces the unreliable metrics-scraping heuristic with deterministic tracking.
    The caller (agent or code) calls register() when a skill/gene is used.
    """

    def __init__(self):
        self._invoked: set[str] = set()

    def register(self, gene_name: str) -> None:
        """Record that a gene was invoked."""
        self._invoked.add(gene_name)

    def clear(self) -> None:
        """Reset for a new converge run."""
        self._invoked.clear()

    def get_invoked(self) -> list[str]:
        """Return sorted list of invoked gene names."""
        return sorted(self._invoked)

    def __contains__(self, gene_name: str) -> bool:
        return gene_name in self._invoked

    def __len__(self) -> int:
        return len(self._invoked)


# Module-level singleton — NOT thread-safe. Concurrent callers (parallel tests,
# async) will mix results. Acceptable for single-threaded CLI use.
_registry = InvocationRegistry()


def register_gene_invocation(gene_name: str) -> None:
    """Register that a gene/skill was invoked during the current converge run.

    Call this from improve() or from agent protocol when a skill is used.
    This is the PRIMARY invocation tracking mechanism.
    """
    _registry.register(gene_name)


def clear_invocation_registry() -> None:
    """Clear the registry at the start of a new converge run."""
    _registry.clear()


def get_invoked_genes() -> list[str]:
    """Return genes registered as invoked during the current run."""
    return _registry.get_invoked()


# =========================================================================
# Data Structures
# =========================================================================

@dataclass
class DomainFitness:
    """Per-CUBE-domain fitness for a Gene."""
    domain_scores: dict[str, float] = field(default_factory=dict)

    @property
    def primary_domain(self) -> str:
        if not self.domain_scores:
            return "general"
        return max(self.domain_scores, key=self.domain_scores.get)

    @property
    def breadth(self) -> int:
        return sum(1 for v in self.domain_scores.values() if v > 0.3)

    @property
    def aggregate(self) -> float:
        if not self.domain_scores:
            return 0.0
        return sum(self.domain_scores.values()) / len(self.domain_scores)


@dataclass
class EvaluationPolicy:
    """Evolvable fitness evaluation parameters.

    The Genome carries its own evaluation policy. Selection pressure on
    the genome level favors genomes whose policies produce better outcomes.
    """
    params: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_EVAL_POLICY))

    def get(self, key: str) -> float:
        return self.params.get(key, DEFAULT_EVAL_POLICY.get(key, 0.0))

    def mutate(self, mutation_strength: float = 0.01, seed: Optional[int] = None) -> "EvaluationPolicy":
        """Return a mutated copy. Each param shifted by gaussian noise.

        Args:
            mutation_strength: Standard deviation for gaussian noise.
            seed: Optional random seed for deterministic tests.
        """
        import random
        rng = random.Random(seed)
        new_params = {}
        for key, value in self.params.items():
            noise = rng.gauss(0, mutation_strength)
            new_params[key] = round(value + noise, 6)
        return EvaluationPolicy(params=new_params)


@dataclass
class AblationScore:
    """Tracks a gene's causal contribution via statistical ablation.

    ablation_lift = P(success | gene invoked) - P(success | gene NOT invoked)

    Positive lift = gene causally contributes to success.
    Zero lift = hitchhiker (correlation without causation).
    Negative lift = gene actively harms outcomes.
    """
    invoked_successes: int = 0
    invoked_failures: int = 0
    not_invoked_successes: int = 0
    not_invoked_failures: int = 0

    @property
    def invoked_total(self) -> int:
        return self.invoked_successes + self.invoked_failures

    @property
    def not_invoked_total(self) -> int:
        return self.not_invoked_successes + self.not_invoked_failures

    @property
    def invoked_success_rate(self) -> float:
        if self.invoked_total == 0:
            return 0.0
        return self.invoked_successes / self.invoked_total

    @property
    def baseline_success_rate(self) -> float:
        if self.not_invoked_total == 0:
            return 0.0
        return self.not_invoked_successes / self.not_invoked_total

    @property
    def ablation_lift(self) -> float:
        if self.invoked_total < 3 or self.not_invoked_total < 3:
            return 0.0
        return self.invoked_success_rate - self.baseline_success_rate

    def record(self, was_invoked: bool, satisfied: bool) -> None:
        if was_invoked and satisfied:
            self.invoked_successes += 1
        elif was_invoked and not satisfied:
            self.invoked_failures += 1
        elif not was_invoked and satisfied:
            self.not_invoked_successes += 1
        else:
            self.not_invoked_failures += 1


@dataclass
class Gene:
    """A single heritable unit in the GRIP genome.

    Each Gene corresponds to a skill, agent, rule, or lib component.
    Fitness is survival-based: did this gene contribute to successful
    outcomes across multiple independent sessions?
    """
    name: str
    content_hash: str = ""
    component_type: str = "skill"
    _base_fitness: float = NEUTRAL_FITNESS
    lineage: Optional[str] = None
    generation_born: int = 0
    mutations: int = 0
    expression_contexts: list[str] = field(default_factory=list)
    invocation_count: int = 0
    contribution_record: list[tuple[str, bool]] = field(default_factory=list)
    last_invoked: str = ""
    last_decayed: str = ""  # UTC isoformat of last time-decay application (empty = never)
    parameters: dict[str, Any] = field(default_factory=dict)
    protected: bool = False
    domain_fitness: DomainFitness = field(default_factory=DomainFitness)
    kono_attestations: int = 0
    kono_permanent: bool = False
    ablation: AblationScore = field(default_factory=AblationScore)
    compressed_content: str = ""  # base64(zlib(utf-8 content)), empty = not hydrated

    @property
    def fitness_floor(self) -> float:
        if self.protected:
            return PROTECTED_FLOOR
        if self.kono_permanent:
            return KONO_PERMANENT_FLOOR
        if self.invocation_count >= 1:
            return SURVIVAL_FLOOR  # Proven genes cannot decay to death
        return 0.0

    @property
    def fitness(self) -> float:
        raw = self._base_fitness
        if self.domain_fitness.domain_scores:
            raw = 0.6 * self._base_fitness + 0.4 * self.domain_fitness.aggregate
        return max(self.fitness_floor, min(1.0, raw))

    @fitness.setter
    def fitness(self, value: float) -> None:
        self._base_fitness = max(0.0, min(1.0, value))

    @property
    def tier(self) -> str:
        f = self.fitness
        if f >= 0.8:
            return "essential"
        if f >= 0.5:
            return "active"
        if f >= DEAD_THRESHOLD:
            return "dormant"
        return "dead"

    def as_converge_point(self) -> dict:
        """Return gene as a ConvergePoint — the polyglot encoding.

        Every gene IS a pre-computed convergence result. This method
        makes that structure explicit: same shape at every fractal level.

        Returns:
            {"id": "gene:<name>", "criterion_ref": "<domain>",
             "fitness": float, "depth": 0, "children": []}
        """
        return {
            "id": f"gene:{self.name}",
            "criterion_ref": self.domain_fitness.primary_domain,
            "fitness": round(self.fitness, 4),
            "depth": 0,
            "children": [],
        }


CONTENT_COMPRESS_THRESHOLD = 256  # Only compress if content > 256 bytes


def compress_content(text: str) -> str:
    """Compress text to base64-encoded zlib. Returns empty string for short/empty content."""
    if not text or len(text) < CONTENT_COMPRESS_THRESHOLD:
        return text  # Store short content as-is
    compressed = zlib.compress(text.encode("utf-8"), level=9)
    return base64.b64encode(compressed).decode("ascii")


def decompress_content(stored: str) -> str:
    """Decompress stored content. Handles both compressed (base64+zlib) and plain text."""
    if not stored:
        return ""
    try:
        raw = base64.b64decode(stored)
        return zlib.decompress(raw).decode("utf-8")
    except Exception:
        return stored  # Already plain text (short content)


@dataclass
class Genome:
    """The complete set of active genes in a GRIP instance."""
    generation: int = 0
    genes: dict[str, Gene] = field(default_factory=dict)
    parent_id: str = ""
    id: str = ""
    created_at: str = ""
    instance_id: str = ""
    total_converge_runs: int = 0
    successful_runs: int = 0
    eval_policy: EvaluationPolicy = field(default_factory=EvaluationPolicy)
    mutation_rate: float = 0.1
    fitness_history: list[float] = field(default_factory=list)
    last_decay_at: str = ""  # ISO date (YYYY-MM-DD) of last time-decay application

    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.now(timezone.utc).isoformat()
        if not self.id:
            self.id = self._compute_id()

    def _compute_id(self) -> str:
        data = sorted((g.name, round(g.fitness, 4)) for g in self.genes.values())
        return hashlib.sha256(json.dumps(data).encode()).hexdigest()[:12]

    def express(self, context: str = "") -> list[Gene]:
        """Express genes matching the given context.

        Supports wave-phase context tags: "wave:ascent:W3", "wave:descent:W2".
        A gene with expression_contexts=["wave:ascent:W3"] will only express
        when context matches that wave phase. Genes without expression_contexts
        express in all contexts (backward compatible).
        """
        result = []
        for gene in self.genes.values():
            if not gene.expression_contexts:
                result.append(gene)
            elif context and context in gene.expression_contexts:
                result.append(gene)
            elif context and context.startswith("wave:"):
                # Wave-phase matching: "wave:ascent:W3" matches "wave:ascent"
                # and "wave:ascent:W3" exactly
                phase_prefix = ":".join(context.split(":")[:2])
                if any(
                    ec == context or ec == phase_prefix
                    for ec in gene.expression_contexts
                ):
                    result.append(gene)
        return result

    def express_for_domain(self, domain: str, context: str = "") -> list[Gene]:
        expressed = self.express(context)
        return sorted(
            expressed,
            key=lambda g: g.domain_fitness.domain_scores.get(domain, 0.0),
            reverse=True,
        )

    def fitness_vector(self) -> dict[str, float]:
        return {name: gene.fitness for name, gene in self.genes.items()}

    def living_genes(self) -> list[Gene]:
        return [g for g in self.genes.values() if g.fitness > DEAD_THRESHOLD]

    def dead_genes(self) -> list[Gene]:
        return [g for g in self.genes.values() if g.fitness <= DEAD_THRESHOLD and not g.protected]

    def oracle_recommend(
        self,
        domain: str = "",
        top_k: int = 3,
        enrich_from_kono: bool = True,
    ) -> list[Gene]:
        """Return top-K genes sorted by fitness for oracle-mode lookup.

        In oracle mode, this replaces express() + converge loop.
        The caller gets the best genes and their compressed content
        without spending tokens on iteration.

        Selection criteria:
        1. Living genes only (fitness > DEAD_THRESHOLD)
        2. If domain specified: sort by domain_fitness[domain]
        3. Else: sort by aggregate fitness
        4. Return top_k with compressed_content hydrated
        5. Optionally enrich with KONO history for cross-session context
        """
        living = self.living_genes()
        if not living:
            return []

        if domain:
            living.sort(
                key=lambda g: g.domain_fitness.domain_scores.get(domain, 0.0),
                reverse=True,
            )
        else:
            living.sort(key=lambda g: g.fitness, reverse=True)

        top = living[:top_k]

        # Enrich top-K with KONO history (bidirectional bridge)
        if enrich_from_kono:
            try:
                import sys
                from pathlib import Path
                _lib = str(Path(__file__).resolve().parent.parent)
                if _lib not in sys.path:
                    sys.path.insert(0, _lib)
                from lib.subsystem_bridge import recall_gene_history_from_kono
                for gene in top:
                    history = recall_gene_history_from_kono(gene.name, limit=5)
                    if history:
                        # Attach KONO history as transient metadata
                        gene._kono_history = history
            except (ImportError, Exception):
                pass

        return top

    def as_converge_tree(self) -> dict:
        """Return genome as a tree of ConvergePoints grouped by domain.

        High-fitness genes become domain roots; lower-fitness peers nest beneath.
        The genome IS the polyglot — same structure at every fractal level.

        Returns:
            {"id": "genome:<id>", "generation": int,
             "domains": {domain: {"root": ConvergePoint, "children": [...]}}}
        """
        # Group living genes by primary domain
        domain_groups: dict[str, list[Gene]] = {}
        for gene in self.living_genes():
            domain = gene.domain_fitness.primary_domain
            domain_groups.setdefault(domain, []).append(gene)

        domains = {}
        for domain, genes in domain_groups.items():
            genes.sort(key=lambda g: g.fitness, reverse=True)
            root = genes[0].as_converge_point()
            root["children"] = [g.as_converge_point() for g in genes[1:]]
            root["depth"] = 1  # Root of domain subtree
            domains[domain] = root

        return {
            "id": f"genome:{self.id}",
            "generation": self.generation,
            "avg_fitness": round(self.avg_fitness, 4),
            "domains": domains,
        }

    def oracle_tier_summary(self) -> list[dict]:
        """Ultra-compact gene representation for oracle mode.

        Each gene collapses to ~30 tokens for maximum context efficiency.
        """
        return [
            {
                "id": f"gene:{g.name}",
                "c": f"{g.domain_fitness.primary_domain}",
                "f": round(g.fitness, 2),
            }
            for g in sorted(self.living_genes(), key=lambda g: g.fitness, reverse=True)
        ]

    @property
    def avg_fitness(self) -> float:
        living = self.living_genes()
        if not living:
            return 0.0
        return round(sum(g.fitness for g in living) / len(living), 4)

    def success_rate(self) -> float:
        if self.total_converge_runs == 0:
            return 0.0
        return self.successful_runs / self.total_converge_runs

    def adaptive_mutation_rate(self, window: int = 5) -> float:
        if len(self.fitness_history) < window:
            return self.mutation_rate

        recent = self.fitness_history[-window:]
        n = len(recent)
        x_mean = (n - 1) / 2.0
        y_mean = sum(recent) / n
        numerator = sum((i - x_mean) * (y - y_mean) for i, y in enumerate(recent))
        denominator = sum((i - x_mean) ** 2 for i in range(n))

        if denominator == 0:
            slope = 0.0
        else:
            slope = numerator / denominator

        if abs(slope) < 0.005:
            rate = min(0.5, self.mutation_rate * 1.5)
        elif slope > 0.01:
            rate = max(0.01, self.mutation_rate * 0.7)
        elif slope < -0.01:
            rate = min(0.4, self.mutation_rate * 1.2)
        else:
            rate = self.mutation_rate

        self.mutation_rate = round(rate, 4)
        return self.mutation_rate


@dataclass
class ConvergeOutcome:
    """Links a converge() result to the genome state during that run."""
    run_id: str
    genome_id: str = ""
    generation: int = 0
    expressed_genes: list[str] = field(default_factory=list)
    invoked_genes: list[str] = field(default_factory=list)
    satisfied: bool = False
    score_before: float = 0.0
    score_after: float = 0.0
    improvement: float = 0.0
    depth_reached: int = 0
    elapsed_sec: float = 0.0
    timestamp: str = ""
    context: str = ""
    cube_scores: dict[str, float] = field(default_factory=dict)

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()
        self.improvement = self.score_after - self.score_before


@dataclass
class ReviewVerdict:
    """Structured LLM attribution after a converge run.

    NOT a quality score (avoids 91-attractor).
    IS a causal attribution: which genes drove the outcome?

    Attributions must sum to <= 1.0 (enforced by normalize()).
    """
    attributions: dict[str, float] = field(default_factory=dict)
    negative_attributions: dict[str, float] = field(default_factory=dict)
    proposed_skills: list[dict] = field(default_factory=list)
    reasoning: str = ""
    timestamp: str = ""
    run_id: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()

    def normalize(self) -> None:
        total = sum(self.attributions.values())
        if total > 1.0:
            for k in self.attributions:
                self.attributions[k] = round(self.attributions[k] / total, 4)
        for k in self.negative_attributions:
            self.negative_attributions[k] = max(-1.0, min(0.0, self.negative_attributions[k]))

    def get_weight(self, gene_name: str) -> float:
        if gene_name in self.negative_attributions:
            return self.negative_attributions[gene_name]
        return self.attributions.get(gene_name, 1.0)
