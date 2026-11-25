# Evolutionary DNA Architecture for converge()

Design specification for `converge_dna.py` -- the evolutionary genome layer
that connects skill survival to convergence outcomes.

## 1. Problem Statement

GRIP has five systems that should form a unified evolutionary loop but are currently disconnected:

| System | What It Does | What It Lacks |
|--------|-------------|---------------|
| `converge.py` | Iterates state toward a criterion | No awareness of which skills contributed |
| `converge_metrics.py` | Records scores and trajectories | No link between scores and skill activity |
| `converge_inherit.py` | Transfers strategies across projects | No genome-level representation |
| `evolutionary_refactor.py` | Measures component fitness via decay | Not integrated into converge loop |
| `kono.py` | Instance DNA with reproduction | Skills listed but not fitness-tracked per skill |

The gap: no system connects **which skills were active** during a converge run
to **whether that run succeeded**, and feeds the result back as selection pressure.

## 2. Biological Analogy (held rigorously)

| Biology | GRIP | Implementation |
|---------|------|----------------|
| Genome | Set of all skills/agents/rules | `Genome` dataclass |
| Gene | Single skill with fitness metadata | `Gene` dataclass |
| Allele | Variant of a skill (parameter set) | `Gene.parameters` dict |
| Genotype | The DNA itself (slow-changing) | Skills on disk + `Genome` snapshot |
| Epigenome | Gene regulation (which genes express) | CLAUDE.md rules, mode configs |
| Phenotype | Observable behavior in a session | `ConvergenceResult` + user satisfaction |
| Fitness | Survival probability | `Gene.fitness` (0.0-1.0) |
| Generation | One converge() cycle | `Genome.generation` counter |
| Natural selection | Survival of the fittest | `select()` after converge |
| Mutation | Skill modification | `point_mutation()`, `insertion()`, `deletion()` |
| Crossover | Strategy merging | `crossover()` from `converge_inherit.py` |
| Extinction | Skill archival | `evolutionary_refactor.py` archive flow |

The analogy holds at every level. Skills are genes. converge() is the generation
cycle. Selection pressure comes from actual survival signals, not self-assessment.

## 3. Core Data Structures

### 3.1 Gene

```python
@dataclass
class Gene:
    """A single heritable unit in the GRIP genome.

    Each Gene corresponds to a skill, agent, rule, or lib component.
    Fitness is survival-based: did this gene contribute to successful
    outcomes across multiple independent sessions?

    Attributes:
        name: Unique identifier (skill directory name, agent filename stem).
        content_hash: SHA-256 prefix of the gene's content. Changes when
            the skill file is modified (mutation detection).
        component_type: One of "skill", "agent", "rule", "lib".
        fitness: Survival-based fitness score (0.0-1.0). Not self-assessed.
        lineage: Parent gene name if this gene was derived from another.
        generation_born: The genome generation when this gene first appeared.
        mutations: Count of content_hash changes observed across generations.
        expression_contexts: Set of mode names where this gene is active.
            Empty set means "always expressed" (constitutive).
        invocation_count: Total times invoked across all tracked sessions.
        contribution_record: List of (run_id, outcome) pairs recording
            whether this gene was active during successful or failed converge runs.
        last_invoked: ISO timestamp of most recent invocation.
        parameters: Mutable dict of tunable parameters (the "allele").
            Example: a scoring skill might have {"weight": 0.4, "threshold": 85}.
        protected: If True, fitness cannot drop below 0.8 (essential gene).
    """
    name: str
    content_hash: str = ""
    component_type: str = "skill"
    fitness: float = 0.5
    lineage: Optional[str] = None
    generation_born: int = 0
    mutations: int = 0
    expression_contexts: set[str] = field(default_factory=set)
    invocation_count: int = 0
    contribution_record: list[tuple[str, bool]] = field(default_factory=list)
    last_invoked: str = ""
    parameters: dict[str, Any] = field(default_factory=dict)
    protected: bool = False
```

### 3.2 Genome

```python
@dataclass
class Genome:
    """The complete set of active genes in a GRIP instance.

    A Genome is a snapshot of which skills/agents/rules exist, their
    fitness scores, and their contribution history. Genomes are
    versioned by generation number and can be serialized to disk
    for cross-session inheritance.

    The genome is NOT the skills themselves (those live on disk).
    The genome is the FITNESS METADATA about those skills --
    which ones are active, how fit they are, and what they contributed to.

    Attributes:
        generation: Monotonically increasing generation counter.
        genes: Dict mapping gene name to Gene instance.
        parent_id: SHA prefix of the parent genome (for lineage tracking).
        id: SHA-256 prefix computed from gene names + fitnesses.
        created_at: ISO timestamp of genome creation.
        instance_id: GRIP instance ID that created this genome.
        total_converge_runs: Number of converge() runs this genome has participated in.
        successful_runs: Number of runs where criterion was satisfied.
    """
    generation: int = 0
    genes: dict[str, Gene] = field(default_factory=dict)
    parent_id: str = ""
    id: str = ""
    created_at: str = ""
    instance_id: str = ""
    total_converge_runs: int = 0
    successful_runs: int = 0

    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.now(timezone.utc).isoformat()
        if not self.id:
            self.id = self._compute_id()

    def _compute_id(self) -> str:
        """Deterministic ID from sorted gene names and their fitnesses."""
        data = sorted((g.name, round(g.fitness, 4)) for g in self.genes.values())
        return hashlib.sha256(json.dumps(data).encode()).hexdigest()[:12]

    def express(self, context: str = "") -> list[Gene]:
        """Return genes active in the given context (mode).

        Args:
            context: Current mode name (e.g., "code", "security").
                Empty string means "return all constitutive genes".

        Returns:
            List of Gene instances that should be active.
            A gene is expressed if:
            - Its expression_contexts is empty (constitutive), OR
            - context is in its expression_contexts
        """

    def fitness_vector(self) -> dict[str, float]:
        """Return {gene_name: fitness} for all genes. Used for comparison."""

    def living_genes(self) -> list[Gene]:
        """Genes with fitness > 0.2 (above Dead tier threshold)."""

    def dead_genes(self) -> list[Gene]:
        """Genes with fitness <= 0.2 (Dead tier -- extinction candidates)."""
```

### 3.3 ConvergeOutcome

```python
@dataclass
class ConvergeOutcome:
    """Links a converge() result to the genome state during that run.

    This is the critical bridge: it records WHICH genes were active
    (expressed and invoked) during a specific converge run, and WHETHER
    that run succeeded. This data drives fitness updates.

    Attributes:
        run_id: Unique converge run identifier (from converge_metrics).
        genome_id: ID of the genome snapshot at run start.
        generation: Genome generation at run start.
        expressed_genes: Names of genes that were active (loaded/available).
        invoked_genes: Names of genes actually called during the run.
            Subset of expressed_genes. These get stronger fitness signals.
        satisfied: Whether converge() met its criterion.
        score_before: Score at depth 0.
        score_after: Final score at termination.
        improvement: score_after - score_before.
        depth_reached: How many iterations were needed.
        elapsed_sec: Wall-clock time for the run.
        timestamp: When this outcome was recorded.
    """
    run_id: str
    genome_id: str
    generation: int
    expressed_genes: list[str] = field(default_factory=list)
    invoked_genes: list[str] = field(default_factory=list)
    satisfied: bool = False
    score_before: float = 0.0
    score_after: float = 0.0
    improvement: float = 0.0
    depth_reached: int = 0
    elapsed_sec: float = 0.0
    timestamp: str = ""
```

## 4. Survival-Based Fitness Model

The fitness model is the most important design decision. It must avoid the
91-attractor problem (self-scoring bias) by grounding fitness in observable
survival signals rather than self-assessment.

### 4.1 Fitness Signals (ranked by reliability)

| Signal | Delta | Reliability | Source |
|--------|-------|-------------|--------|
| Invoked during successful converge | +0.05 | HIGH | ConvergeOutcome |
| Invoked during failed converge | -0.02 | HIGH | ConvergeOutcome |
| Expressed but NOT invoked during success | +0.01 | MEDIUM | ConvergeOutcome |
| Expressed but NOT invoked during failure | -0.01 | MEDIUM | ConvergeOutcome |
| Invoked outside converge (general use) | +0.03 | HIGH | metrics.jsonl |
| Not invoked for N days | decay(0.95^days) | HIGH | Timestamp math |
| User explicitly pruned | fitness = 0.0 | DEFINITIVE | User action |
| User explicitly protected | floor = 0.8 | DEFINITIVE | PROTECTED set |
| Invoked in diverse contexts (>3 modes) | +0.02 | HIGH | expression_contexts |
| Contributed to faster convergence | +0.03 | HIGH | depth comparison |

### 4.2 Fitness Update Algorithm

```python
def update_gene_fitness(gene: Gene, outcome: ConvergeOutcome) -> float:
    """Update a single gene's fitness based on a converge outcome.

    The update is SMALL and INCREMENTAL. No single run can dramatically
    change a gene's fitness. This prevents oscillation and ensures that
    fitness reflects ACCUMULATED evidence across many runs.

    Args:
        gene: The gene to update.
        outcome: The converge run outcome.

    Returns:
        The new fitness value (also mutated in-place on gene).
    """
    was_invoked = gene.name in outcome.invoked_genes
    was_expressed = gene.name in outcome.expressed_genes

    if not was_expressed:
        # Gene was not even loaded -- no signal
        return gene.fitness

    delta = 0.0

    if was_invoked and outcome.satisfied:
        # Strong positive: invoked and run succeeded
        delta = +0.05
        # Bonus for fast convergence (fewer iterations = more efficient)
        if outcome.depth_reached <= 1:
            delta += 0.02
    elif was_invoked and not outcome.satisfied:
        # Mild negative: invoked but run failed
        # Mild because failure might not be this gene's fault
        delta = -0.02
    elif not was_invoked and outcome.satisfied:
        # Weak positive: available but not needed -- still survived
        delta = +0.01
    elif not was_invoked and not outcome.satisfied:
        # Weak negative: available, not used, run failed
        delta = -0.01

    # Improvement bonus: did the score increase significantly?
    if was_invoked and outcome.improvement > 10:
        delta += 0.03
    elif was_invoked and outcome.improvement < -5:
        delta -= 0.01

    # Apply delta with clamping
    gene.fitness = max(0.0, min(1.0, gene.fitness + delta))

    # Protected floor
    if gene.protected:
        gene.fitness = max(0.8, gene.fitness)

    # Record contribution
    gene.contribution_record.append((outcome.run_id, outcome.satisfied))

    # Update invocation tracking
    if was_invoked:
        gene.invocation_count += 1
        gene.last_invoked = outcome.timestamp

    return gene.fitness
```

### 4.3 Time Decay (inherited from evolutionary_refactor.py)

```python
def apply_time_decay(genome: Genome) -> None:
    """Apply KONO-inspired time decay to all genes.

    Formula: fitness *= 0.95 ^ days_since_last_invocation

    This runs ONCE per session start, not per converge run.
    It ensures that unused genes gradually lose fitness regardless
    of past success.

    This is the same formula used in evolutionary_refactor.py
    (calculate_fitness) and kono.py (GRIPInstance.metabolize).
    The DNA layer uses the same constants for consistency.
    """
    DECAY_RATE = 0.95  # Same as evolutionary_refactor.py
    now = datetime.now(timezone.utc)

    for gene in genome.genes.values():
        if gene.protected:
            continue

        if gene.last_invoked:
            try:
                last = datetime.fromisoformat(gene.last_invoked)
                if last.tzinfo is None:
                    last = last.replace(tzinfo=timezone.utc)
                days = (now - last).total_seconds() / 86400
            except (ValueError, TypeError):
                days = 30.0
        else:
            days = 30.0

        gene.fitness = max(0.0, gene.fitness * (DECAY_RATE ** days))
```

### 4.4 Why NOT Self-Scoring

The 91-attractor taught us that LLM self-assessment converges to a fixed point
regardless of actual quality. The DNA fitness model avoids this by:

1. **No LLM in the fitness loop.** Fitness is computed from timestamps, invocation
   counts, and boolean outcomes (satisfied/not). No model evaluates "how good" a
   skill is.

2. **Survival semantics.** A gene's fitness means "how likely is this gene to still
   be useful?" not "how good is this gene?". The former is measurable; the latter
   invites the attractor.

3. **Incremental updates.** Maximum per-run delta is ~0.10. A gene needs many
   successful runs to reach high fitness. This prevents single lucky runs from
   inflating scores.

4. **Decay as default.** Without positive signals, fitness decays to zero. The
   null hypothesis is that a gene is useless. It must continually prove otherwise.

## 5. Mutation Operators

### 5.1 Point Mutation

```python
def point_mutation(genome: Genome, gene_name: str,
                   param_key: str, delta: float) -> Genome:
    """Tweak one parameter of one gene.

    This is the smallest possible change. Example: adjusting a scoring
    weight from 0.4 to 0.35, or a threshold from 85 to 80.

    Args:
        genome: The genome to mutate (a new copy is returned).
        gene_name: Which gene to mutate.
        param_key: Which parameter to tweak.
        delta: How much to change it (added to current value).

    Returns:
        New Genome with the mutation applied. Original is unchanged.
        The mutated gene's mutations counter is incremented.
        The new genome's generation is incremented.

    Raises:
        KeyError: If gene_name or param_key not found.
    """
```

### 5.2 Insertion

```python
def insertion(genome: Genome, new_gene: Gene) -> Genome:
    """Add a new gene to the genome.

    Called when a new skill is generated (via learning-detector.py or
    optim.sh generate). The new gene starts with fitness 0.5 (neutral)
    and must earn its fitness through actual invocations.

    Args:
        genome: The genome to extend.
        new_gene: The gene to insert. Must have a unique name.

    Returns:
        New Genome with the gene added. Generation incremented.

    Raises:
        ValueError: If a gene with this name already exists.
    """
```

### 5.3 Deletion

```python
def deletion(genome: Genome, gene_name: str) -> Genome:
    """Remove a dead gene from the genome.

    Only genes in the Dead tier (fitness <= 0.2) can be deleted.
    Protected genes cannot be deleted.

    This does NOT delete the skill file from disk -- it removes the
    gene from the active genome. The skill file is archived via
    evolutionary_refactor.py's archive_component().

    Args:
        genome: The genome to shrink.
        gene_name: Which gene to remove.

    Returns:
        New Genome without the gene. Generation incremented.

    Raises:
        ValueError: If gene is protected or fitness > 0.2.
        KeyError: If gene_name not found.
    """
```

### 5.4 Crossover

```python
def crossover(genome_a: Genome, genome_b: Genome,
              fitness_threshold: float = 0.6) -> Genome:
    """Merge high-fitness genes from two genomes.

    This is the genome-level equivalent of converge_inherit.py's
    strategy transfer. When two projects (or two session lineages)
    have developed different successful gene sets, crossover combines
    the best of both.

    Algorithm:
    1. Start with genome_a as the base.
    2. For each gene in genome_b that is NOT in genome_a:
       - If gene.fitness >= fitness_threshold: add it (gene transfer)
    3. For each gene in BOTH genomes:
       - Keep the variant with higher fitness (allele selection)
    4. Return new genome with incremented generation.

    Args:
        genome_a: Primary parent genome.
        genome_b: Secondary parent genome (donor).
        fitness_threshold: Minimum fitness for a gene to transfer.

    Returns:
        New child Genome combining the fittest genes from both parents.
    """
```

## 6. Integration Points

### 6.1 With converge.py -- Genome-Aware Convergence

The key integration: wrapping `converge()` to track gene expression.

```python
def converge_with_genome(
    state: Any,
    criterion: Callable[[Any], bool],
    improve: Callable[[Any], Any],
    genome: Genome,
    max_depth: int = 3,
    _scorer: Optional[Callable[[Any], float]] = None,
    label: str = "",
    context: str = "",
) -> tuple[ConvergenceResult, ConvergeOutcome]:
    """Genome-aware wrapper around converge().

    Before: Snapshots the genome state.
    During: Tracks which genes are expressed (via context/mode).
             Gene invocation tracking happens externally via metrics.jsonl
             hooks -- this function does not intercept skill calls.
    After:  Records ConvergeOutcome and updates genome fitness.

    This function does NOT modify the converge() kernel. It wraps it,
    adding pre/post instrumentation.

    Args:
        state: Initial state for converge().
        criterion: Stop condition.
        improve: Improvement function.
        genome: Current genome to track against.
        max_depth: Maximum iterations.
        _scorer: Optional scoring function.
        label: Human-readable label.
        context: Current mode name for gene expression filtering.

    Returns:
        Tuple of (ConvergenceResult, ConvergeOutcome).
        The ConvergeOutcome contains the gene contribution data.
        Callers should pass the outcome to update_genome_fitness().
    """
    # 1. Snapshot: which genes are expressed in this context
    expressed = [g.name for g in genome.express(context)]

    # 2. Record initial score
    score_before = _scorer(state) if _scorer else 0.0

    # 3. Run converge (unmodified kernel)
    result = converge(
        state=state,
        criterion=criterion,
        improve=improve,
        max_depth=max_depth,
        _scorer=_scorer,
        label=label,
    )

    # 4. Determine which genes were invoked during the run.
    #    This reads metrics.jsonl for skill_invoked events between
    #    the start and end timestamps of the run.
    invoked = _identify_invoked_genes(
        expressed_genes=expressed,
        start_time=...,  # from result.elapsed_sec
        end_time=...,
    )

    # 5. Build outcome
    outcome = ConvergeOutcome(
        run_id=label or str(uuid.uuid4())[:8],
        genome_id=genome.id,
        generation=genome.generation,
        expressed_genes=expressed,
        invoked_genes=invoked,
        satisfied=result.satisfied,
        score_before=score_before,
        score_after=_scorer(result.best_state) if _scorer else 0.0,
        improvement=(_scorer(result.best_state) if _scorer else 0.0) - score_before,
        depth_reached=result.depth_reached,
        elapsed_sec=result.elapsed_sec,
        timestamp=datetime.now(timezone.utc).isoformat(),
    )

    return result, outcome
```

### 6.2 With converge_metrics.py -- Outcome Recording

```python
def record_outcome(outcome: ConvergeOutcome) -> None:
    """Persist a ConvergeOutcome to the converge metrics JSONL.

    Extends the existing converge_metrics event format with genome fields:
    - genome_id
    - generation
    - expressed_genes
    - invoked_genes

    These fields are added to the converge_end event so that compound
    analysis can correlate genome state with outcomes.
    """
```

### 6.3 With evolutionary_refactor.py -- Bidirectional Fitness Sync

```python
def sync_with_evolutionary_refactor(genome: Genome) -> Genome:
    """Synchronize genome fitness with evolutionary_refactor.py's ComponentFitness.

    Two-way sync:
    1. Genome -> EvolutionaryRefactor: Gene fitness scores update
       ComponentFitness.fitness_score for the audit/proposal pipeline.
    2. EvolutionaryRefactor -> Genome: Usage data (use_count, last_used)
       from metrics.jsonl enriches gene invocation tracking.

    This ensures that evolutionary_refactor.py's tier classification
    (Essential/Active/Dormant/Dead) stays consistent with genome fitness,
    and that both systems share the same survival signals.

    The sync is additive: both systems contribute signals, neither overwrites
    the other. The genome is the authoritative fitness source; evolutionary
    refactor provides the usage evidence.

    Args:
        genome: Genome to sync.

    Returns:
        Updated genome with enriched invocation data.
    """
```

### 6.4 With kono.py -- Instance DNA Bridge

```python
def genome_to_instance_dna(genome: Genome) -> InstanceDNA:
    """Convert a Genome to a KONO InstanceDNA.

    The InstanceDNA in kono.py stores skills as a flat list of names.
    This function enriches it with fitness data stored in the patterns dict:
    - patterns["gene_fitness:{name}"] = fitness_score
    - skills = [gene.name for gene in genome.living_genes()]

    This allows KONO's instance reproduction to carry genome fitness
    forward when a GRIPInstance.reproduce() creates a child.

    Args:
        genome: Source genome.

    Returns:
        InstanceDNA populated from genome state.
    """

def instance_dna_to_genome(dna: InstanceDNA, generation: int = 0) -> Genome:
    """Reconstruct a Genome from a KONO InstanceDNA.

    Inverse of genome_to_instance_dna(). Reads fitness scores from
    dna.patterns and skill names from dna.skills.

    Genes not found in dna.patterns get default fitness 0.5.

    Args:
        dna: Source InstanceDNA.
        generation: Generation number to assign.

    Returns:
        Genome reconstructed from InstanceDNA.
    """
```

### 6.5 With learning-detector.py -- Mutation Source

```python
def learning_event_to_mutation(
    genome: Genome,
    candidate: dict,  # Skill candidate from learning-detector.py
) -> Genome:
    """Convert an approved learning candidate into a genome insertion.

    When learning-detector.py produces a skill candidate and the user
    approves it, this function:
    1. Creates a Gene from the candidate metadata.
    2. Inserts it into the genome via insertion().
    3. Sets initial fitness to 0.5 (must earn its place).
    4. Records lineage if the candidate was derived from an existing skill.

    Args:
        genome: Current genome.
        candidate: Approved skill candidate dict from learning-detector.py.

    Returns:
        New genome with the learning-derived gene inserted.
    """
```

## 7. The Evolutionary Loop

### 7.1 Single-Generation Cycle

```python
def evolve_generation(genome: Genome, outcome: ConvergeOutcome) -> Genome:
    """Process one evolutionary generation.

    Called after each converge() run. This is the core evolutionary step:
    1. Update fitness for all genes based on the outcome.
    2. Apply time decay to all genes.
    3. Identify dead genes (candidates for deletion).
    4. Persist the updated genome.

    This is NOT the full evolve() population loop -- it is the
    single-generation fitness update that happens after every converge run.

    Args:
        genome: Current genome before this generation.
        outcome: The converge outcome that drives fitness updates.

    Returns:
        New genome with updated fitness scores and incremented generation.
    """
    new_genome = deepcopy(genome)
    new_genome.generation += 1
    new_genome.parent_id = genome.id
    new_genome.total_converge_runs += 1
    if outcome.satisfied:
        new_genome.successful_runs += 1

    # Update fitness for each gene
    for gene in new_genome.genes.values():
        update_gene_fitness(gene, outcome)

    # Apply time decay (only significant across sessions, but always applied)
    apply_time_decay(new_genome)

    # Recompute genome ID
    new_genome.id = new_genome._compute_id()

    return new_genome
```

### 7.2 Population-Level Evolution (for future multi-genome scenarios)

```python
def evolve(
    population: list[Genome],
    task_fn: Callable[[Genome], ConvergeOutcome],
    generations: int = 10,
    population_size: int = 5,
    mutation_rate: float = 0.1,
    crossover_rate: float = 0.2,
) -> Genome:
    """Run a population-level evolutionary loop.

    This is the highest-level orchestrator. It maintains a POPULATION
    of genomes, runs each through a converge cycle, updates fitness,
    selects the fittest, and reproduces with mutation and crossover.

    In practice, GRIP starts with population_size=1 (single genome).
    Population > 1 occurs when:
    - Multiple projects diverge their genomes (different skill needs)
    - Cross-project inheritance creates genome variants
    - A/B testing of skill configurations

    Algorithm:
    ```
    for gen in range(generations):
        # Evaluate: run converge for each genome
        outcomes = [(genome, task_fn(genome)) for genome in population]

        # Update fitness
        population = [evolve_generation(g, o) for g, o in outcomes]

        # Select: keep top population_size genomes by avg gene fitness
        population = select(population, population_size)

        # Reproduce: crossover + mutation
        offspring = reproduce(population, mutation_rate, crossover_rate)
        population.extend(offspring)

        # Trim back to population_size
        population = select(population, population_size)

    return best_genome(population)
    ```

    Args:
        population: Initial genome population.
        task_fn: Function that runs a converge cycle for a genome and returns outcome.
        generations: Number of evolutionary generations to run.
        population_size: Target population size after selection.
        mutation_rate: Probability of mutating each gene per generation.
        crossover_rate: Probability of crossover between two genomes.

    Returns:
        The fittest genome after all generations.
    """


def select(population: list[Genome], keep: int) -> list[Genome]:
    """Tournament selection: keep the top N genomes by average gene fitness.

    Ranking metric: average fitness of living genes (fitness > 0.2).
    Dead genes are excluded from the average -- they are noise.

    Ties broken by:
    1. Higher successful_runs / total_converge_runs ratio.
    2. More living genes (broader capability).

    Args:
        population: Genomes to select from.
        keep: How many to retain.

    Returns:
        Top N genomes, sorted by fitness (best first).
    """


def reproduce(
    population: list[Genome],
    mutation_rate: float = 0.1,
    crossover_rate: float = 0.2,
) -> list[Genome]:
    """Generate offspring genomes via crossover and mutation.

    For each pair of adjacent genomes in the population:
    1. With probability crossover_rate: produce a crossover child.
    2. For each gene in the child, with probability mutation_rate:
       apply a point_mutation with a small random delta.

    Args:
        population: Parent genomes (assumed sorted by fitness, best first).
        mutation_rate: Per-gene mutation probability.
        crossover_rate: Per-pair crossover probability.

    Returns:
        List of offspring genomes (may be empty if no reproduction occurs).
    """
```

## 8. Persistence

### 8.1 Genome Store

```
~/.claude/cache/genome.json          # Current active genome
~/.claude/cache/genome-history.jsonl  # Append-only log of genome snapshots
~/.claude/cache/genome-outcomes.jsonl # Append-only log of ConvergeOutcomes
```

### 8.2 Serialization

```python
def save_genome(genome: Genome, path: Optional[Path] = None) -> Path:
    """Serialize genome to JSON.

    Writes to ~/.claude/cache/genome.json (current) and appends
    a snapshot to genome-history.jsonl for lineage tracking.

    Args:
        genome: Genome to persist.
        path: Override path (default: GENOME_PATH).

    Returns:
        Path where genome was saved.
    """


def load_genome(path: Optional[Path] = None) -> Optional[Genome]:
    """Load genome from JSON.

    Returns None if no genome file exists (first run).

    Args:
        path: Override path (default: GENOME_PATH).

    Returns:
        Loaded Genome or None.
    """


def save_outcome(outcome: ConvergeOutcome) -> None:
    """Append outcome to genome-outcomes.jsonl."""


def load_outcomes(limit: int = 100) -> list[ConvergeOutcome]:
    """Load recent outcomes from genome-outcomes.jsonl.

    Args:
        limit: Maximum number of recent outcomes to load.

    Returns:
        List of outcomes, most recent first.
    """
```

## 9. Genome Initialization

On first run (no `genome.json` exists), the genome must be bootstrapped
from the current filesystem state.

```python
def bootstrap_genome() -> Genome:
    """Create initial genome by scanning the filesystem.

    Reads all skills, agents, rules, and lib files. Creates a Gene for
    each with:
    - content_hash from file contents
    - fitness = 0.5 (neutral starting point)
    - protected = True if in evolutionary_refactor.py's PROTECTED set
    - component_type inferred from directory location

    Enriches with usage data from evolutionary_refactor.py's measure_usage().

    This is called ONCE on first run. Subsequent sessions load from genome.json.

    Returns:
        Fresh genome with generation=0 and all current components as genes.
    """
```

## 10. File Organization

```
lib/
  converge.py              # UNCHANGED -- the kernel
  converge_dna.py          # NEW -- this design
  converge_metrics.py      # EXTENDED -- adds genome fields to events
  converge_inherit.py      # UNCHANGED -- crossover() calls into it
  evolutionary_refactor.py # UNCHANGED -- sync_with_evolutionary_refactor() reads it
  kono.py                  # UNCHANGED -- genome_to_instance_dna() bridges to it

cache/
  genome.json              # Current active genome
  genome-history.jsonl     # Genome snapshots across generations
  genome-outcomes.jsonl    # ConvergeOutcome records
```

The design principle is **extend, not break**. `converge_dna.py` is a new file
that imports from existing modules. The only modification to an existing file
is adding optional genome fields to `converge_metrics.py`'s event recording.

## 11. Usage Patterns

### 11.1 Session Start (Automatic)

```python
# In session-start hook or GRIP resurrection
genome = load_genome()
if genome is None:
    genome = bootstrap_genome()
    save_genome(genome)
else:
    apply_time_decay(genome)
    genome = sync_with_evolutionary_refactor(genome)
```

### 11.2 During Converge Run

```python
# When /converge command is invoked
result, outcome = converge_with_genome(
    state=initial_state,
    criterion=score_ok,
    improve=apply_feedback,
    genome=genome,
    label="pr-review",
    context=current_mode,
)

# Update genome
genome = evolve_generation(genome, outcome)
save_genome(genome)
save_outcome(outcome)
record_outcome(outcome)  # to converge_metrics JSONL
```

### 11.3 After Learning Event

```python
# When a skill candidate is approved
genome = learning_event_to_mutation(genome, approved_candidate)
save_genome(genome)
```

### 11.4 Cross-Project Inheritance

```python
# When starting work on a new project
other_genome = load_genome(path=other_project_genome_path)
if other_genome:
    genome = crossover(genome, other_genome, fitness_threshold=0.7)
    save_genome(genome)
```

### 11.5 Evolutionary Refactoring

```python
# When running evolutionary_refactor.py audit
dead = genome.dead_genes()
for gene in dead:
    # Archive the skill file via evolutionary_refactor
    archive_component(gene.name, gene.component_type, "fitness below 0.2")
    # Remove from genome
    genome = deletion(genome, gene.name)
save_genome(genome)
```

## 12. Invariants and Constraints

1. **converge() kernel is never modified.** All DNA integration is via wrapping.
2. **Fitness is never set by LLM judgment.** Only by timestamps, booleans, and counts.
3. **Mutations return new genomes.** Genome instances are treated as immutable snapshots.
   The `evolve_generation()` function returns a new genome; it does not mutate in place.
4. **Protected genes cannot die.** Fitness floor of 0.8, cannot be deleted.
5. **Maximum fitness delta per run is bounded.** No single run moves fitness more than ~0.10.
6. **Time decay is the default trajectory.** Without positive evidence, all genes decay to zero.
7. **Genome serialization is append-only for history.** `genome-history.jsonl` is never truncated.
8. **Gene names are unique within a genome.** Enforced by dict key.
9. **Crossover never reduces fitness.** Genes are only added or replaced with higher-fitness variants.
10. **The population starts at size 1.** Multi-genome evolution is an opt-in scaling mechanism.

## 13. Metrics and Observability

### 13.1 Genome Health Dashboard

```python
def genome_health(genome: Genome) -> dict:
    """Report genome health metrics.

    Returns:
        Dict with:
        - total_genes: int
        - living_genes: int
        - dead_genes: int
        - avg_fitness: float
        - tier_distribution: {essential: N, active: N, dormant: N, dead: N}
        - success_rate: successful_runs / total_converge_runs
        - generation: int
        - top_genes: list of (name, fitness) sorted by fitness desc
        - bottom_genes: list of (name, fitness) sorted by fitness asc
        - diversity: number of unique expression_contexts across all genes
    """
```

### 13.2 Lineage Visualization

```python
def format_lineage(genome: Genome, depth: int = 5) -> str:
    """Format genome lineage as ASCII tree.

    Reads genome-history.jsonl to trace parent_id chains.

    Example output:
        Gen 0  [a3f2c1d8] 45 genes, avg=0.50
        Gen 5  [b7e4a2c9] 43 genes, avg=0.58 (+8%)
        Gen 12 [c1d8f3a2] 41 genes, avg=0.65 (+12%)
        Gen 19 [d4c7b1e3] 39 genes, avg=0.71 (+9%)
        Gen 24 [e8a3d2f1] 38 genes, avg=0.74 (+4%)  <-- current

    Args:
        genome: Current genome.
        depth: How many ancestors to show.

    Returns:
        Formatted lineage string.
    """
```

## 14. Testing Strategy

### 14.1 Unit Tests

```
tests/test_converge_dna.py

- test_gene_fitness_update_success: invoked + satisfied = fitness increase
- test_gene_fitness_update_failure: invoked + not satisfied = fitness decrease
- test_gene_fitness_update_expressed_only: expressed but not invoked = small change
- test_gene_fitness_protected_floor: protected gene never drops below 0.8
- test_gene_fitness_incremental: 50 updates, verify no single jump > 0.10
- test_time_decay: 30 days inactive = significant fitness reduction
- test_point_mutation: parameter change recorded, mutations counter incremented
- test_insertion: new gene added, duplicate name raises ValueError
- test_deletion: dead gene removed, protected gene raises ValueError
- test_crossover: high-fitness genes transfer, low-fitness genes don't
- test_genome_express: correct genes returned for given context
- test_genome_serialization: save + load roundtrip preserves all fields
- test_bootstrap_genome: filesystem scan produces valid genome
- test_evolve_generation: generation incremented, fitness updated, outcome recorded
- test_kono_bridge: genome <-> InstanceDNA roundtrip
- test_no_self_scoring: verify no LLM call in fitness computation
```

### 14.2 Integration Tests

```
tests/test_converge_dna_integration.py

- test_converge_with_genome: full converge run produces valid outcome
- test_evolutionary_refactor_sync: fitness values consistent between systems
- test_multi_generation_evolution: 10 generations, verify fitness convergence
- test_crossover_inheritance: two divergent genomes merge correctly
- test_learning_to_mutation: approved candidate becomes gene
```

## 15. Migration Path

### Phase 1: Data Layer (converge_dna.py) -- COMPLETE
- Gene, Genome, ConvergeOutcome dataclasses
- Persistence (save/load genome, outcomes, history)
- bootstrap_genome() from live filesystem (158 genes)
- Survival-based fitness update algorithm (no LLM self-scoring)
- Mutation operators (point_mutation, insertion, deletion, crossover)
- AblationScore for hitchhiker gene detection
- EvaluationPolicy for meta-learning of fitness parameters
- Adaptive mutation rate via linear regression slope
- Nursery protection (5 generations decay immunity)
- 88 unit tests + multi-generation simulation

### Phase 2: Integration Layer -- COMPLETE
- converge_with_genome() wrapper
- sync_with_evolutionary_refactor() bidirectional bridge
- KONO bridge functions (genome_to_instance_dna, instance_dna_to_genome)
- learning_event_to_mutation() for insight-driven gene creation
- converge_metrics.py extended with genome_id, expressed_genes, invoked_genes
- Fitness bridge file (cache/fitness-bridge.json) for cross-system reads
- KONO bridge file (cache/genome-kono-bridge.json) for cross-session memory

### Phase 3: Hook Wiring -- COMPLETE
- session-start.sh: bootstrap-or-load genome with time decay on init
- session-end.sh: sync + decay + kono-sync before serialization
- evolve_generation() processes each converge outcome
- genome_health() and cube_genome_health() observability dashboards
- CLI: bootstrap, bootstrap-or-load, health, cube, lineage, status, decay, sync, kono-sync

### Phase 4: Population Evolution (future)
- select(), reproduce(), evolve() are implemented but not yet wired
- Cross-project genome inheritance via converge_inherit.py bridge
- A/B testing infrastructure for genome variants

### Phase 5: LLM Attribution Layer -- COMPLETE
- ReviewVerdict dataclass for structured causal attribution
- should_review() conditional trigger (significant improvement, regression, stall)
- build_review_prompt() + parse_review_response() for structured LLM attribution
- apply_review_verdict() modifies mechanical fitness by attribution weights
- propose_micro_skill() writes minimal SKILL.md files for genesis
- apply_genesis_proposals() processes all proposals from a verdict
- evolve_generation() accepts optional ReviewVerdict parameter
- review-verdicts.jsonl persistence for analysis
- Step 7 added to converge SKILL.md protocol
- Fully backwards compatible: no verdict = identical to Phase 3 behavior

Phases 1-3 and 5 are complete. Phase 4 is architectural preparation for a future
where GRIP manages multiple projects with divergent genome needs.

## 16. KONO Integration: Genome Memory Substrate

KONO's 4D memory substrate augments the DNA archetype as **long-term genetic memory**.
Where `genome.json` stores the current genome snapshot, KONO stores the
evolutionary HISTORY — patterns of which genes succeed in which contexts,
attested across sessions.

### 16.1 KONO as Genetic Memory

```python
def store_gene_memory(gene: Gene, outcome: ConvergeOutcome) -> None:
    """Store gene contribution as a KONO memory.

    The memory captures: "Gene X was invoked during converge run Y,
    which succeeded/failed with score Z in context C."

    KONO coordinates map to genome coordinates:
    - project → genome instance_id
    - topic → gene.component_type + outcome.context
    - abstraction → "gene_contribution" (concrete vs meta-strategy)
    - time → outcome.timestamp

    When multiple sessions attest the same gene's value (KONO attestation
    threshold = 3), the memory becomes permanent. This means:
    - The gene's fitness history is immune to session-level noise
    - Cross-session evidence accumulates automatically
    - Permanent memories anchor gene fitness against short-term decay
    """

def recall_gene_fitness_history(gene_name: str) -> list[dict]:
    """Recall a gene's fitness history from KONO.

    Searches KONO for all memories tagged with this gene name.
    Returns chronological fitness events, including:
    - Which converge runs the gene participated in
    - Whether those runs succeeded or failed
    - Attestation count (how many sessions confirmed the gene's value)
    - Whether the memory is permanent (attested 3+ times)

    This is the "genetic memory" — the system remembers which genes
    were valuable even across context window resets.
    """
```

### 16.2 KONO Attestation as Fitness Anchor

```python
KONO_ATTESTATION_BONUS = 0.05  # Per attestation, max 3 = +0.15
KONO_PERMANENT_FLOOR = 0.6    # Permanent KONO memories set a fitness floor

def apply_kono_attestation(gene: Gene, kono_memories: list[dict]) -> None:
    """Apply KONO attestation signals to gene fitness.

    When a gene's contribution memory has been attested by multiple
    independent sessions:
    - 1 attestation: +0.05 fitness bonus (observed once)
    - 2 attestations: +0.10 fitness bonus (confirmed)
    - 3+ attestations: permanent memory + fitness floor of 0.6

    This prevents valuable genes from decaying to zero during
    periods of inactivity. The gene earned its permanent status
    through cross-session evidence, not self-assessment.

    The KONO attestation mechanism is the evolutionary equivalent of
    epigenetic imprinting — a gene that has been proven across multiple
    independent "lifetimes" (sessions) gets a survival advantage
    that persists beyond any single generation.
    """
```

### 16.3 KONO Memory Metabolism and Gene Decay Alignment

```python
def align_kono_metabolism(genome: Genome) -> None:
    """Align KONO memory metabolism with genome time decay.

    Both systems use 0.95^days decay. This function ensures they
    stay synchronized:
    - Gene fitness decays at 0.95^days (from Section 4.3)
    - KONO memory fitness decays at 0.95^days (from kono.py)
    - When a gene's KONO memories are metabolized away, the gene
      loses its attestation-based fitness floor
    - When a gene is deleted (extinction), its KONO memories are
      marked as orphaned (faster decay)

    The two decay rates are identical BY DESIGN — this makes the
    genome and its memory substrate age at the same rate, preventing
    stale memories from inflating gene fitness.
    """
```

## 17. CUBE Integration: Domain-Specific Gene Fitness

CUBE measures quality across design principle domains (DRY, SOLID, KISS,
YAGNI, etc.). The DNA archetype extends Gene fitness from a single scalar
to a **domain fitness vector** — each gene knows which quality domains
it strengthens.

### 17.1 Domain Fitness Vector

```python
# CUBE domains that genes can contribute to
CUBE_DOMAINS = [
    "DRY",        # Don't Repeat Yourself
    "SOLID",      # Single Responsibility, Open/Closed, etc.
    "KISS",       # Keep It Simple
    "YAGNI",      # You Ain't Gonna Need It
    "GRASP",      # Responsibility assignment
    "efficiency", # Token/compute efficiency
]

@dataclass
class DomainFitness:
    """Per-domain fitness for a Gene.

    Instead of a single fitness float, each gene tracks HOW it
    contributes to quality across CUBE domains. This enables:
    1. Domain-specific gene expression (use DRY-fit genes for DRY tasks)
    2. Targeted crossover (transfer genes that improve specific domains)
    3. Balanced genome health (ensure coverage across all domains)

    Attributes:
        domain_scores: Dict mapping CUBE domain to fitness (0.0-1.0).
        primary_domain: The domain this gene contributes to most.
            Computed from highest domain_scores entry.
        breadth: Number of domains where fitness > 0.3.
            Higher breadth = more generalist gene.
    """
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
        """Weighted average across domains. This is the scalar fitness."""
        if not self.domain_scores:
            return 0.5
        return sum(self.domain_scores.values()) / len(self.domain_scores)
```

### 17.2 CUBE-Aware Fitness Update

```python
def update_gene_domain_fitness(
    gene: Gene,
    outcome: ConvergeOutcome,
    cube_scores: Optional[dict[str, float]] = None,
) -> None:
    """Update a gene's domain fitness based on converge outcome + CUBE scores.

    When a converge run includes CUBE domain scores (e.g., from a code
    quality audit), genes that were invoked during that run get domain-
    specific fitness updates.

    Example: A converge run improves DRY score from 0.6 to 0.9.
    Genes invoked during that run get a DRY fitness boost proportional
    to the improvement (+0.30 * scaling_factor).

    If no CUBE scores are available, falls back to the scalar fitness
    update from Section 4.2.

    Args:
        gene: Gene to update.
        outcome: Converge outcome with score data.
        cube_scores: Optional CUBE domain scores from the run.
            Dict mapping domain name to score (0.0-1.0).
    """
```

### 17.3 Domain-Aware Gene Expression

```python
def express_for_domain(genome: Genome, domain: str, context: str = "") -> list[Gene]:
    """Select genes optimized for a specific CUBE domain.

    When the converge loop is optimizing for a specific quality domain
    (e.g., "DRY" during a deduplication task), this function returns
    genes sorted by their fitness in that domain.

    This is epigenetic regulation: the same genome expresses different
    gene subsets depending on which quality domain is being targeted.

    Algorithm:
    1. Filter by context (mode) as in genome.express()
    2. Sort by domain_fitness[domain] descending
    3. Return top genes (those above domain fitness threshold)

    Args:
        genome: The genome to express from.
        domain: CUBE domain to optimize for.
        context: Current mode name for additional filtering.

    Returns:
        List of genes sorted by domain-specific fitness, most fit first.
    """
```

### 17.4 CUBE Genome Health Report

```python
def cube_genome_health(genome: Genome) -> dict:
    """Report genome health across CUBE domains.

    Returns a matrix: which domains are well-covered by high-fitness
    genes, and which domains have fitness gaps.

    Example output:
    {
        "DRY": {"avg_fitness": 0.72, "gene_count": 12, "top_gene": "dry-enforcer"},
        "SOLID": {"avg_fitness": 0.65, "gene_count": 8, "top_gene": "solid-enforcer"},
        "KISS": {"avg_fitness": 0.45, "gene_count": 3, "top_gene": "yagni-enforcer"},
        "coverage_gaps": ["KISS", "GRASP"],
        "strongest_domain": "DRY",
        "weakest_domain": "KISS",
    }

    This identifies evolutionary pressure points: domains where the
    genome needs new genes (mutation/insertion targets).
    """
```

## 18. Updated Gene Dataclass (with KONO + CUBE)

```python
@dataclass
class Gene:
    """A single heritable unit in the GRIP genome.

    Extended with KONO memory integration and CUBE domain fitness.

    New fields (vs Section 3.1):
        domain_fitness: DomainFitness tracking per-CUBE-domain scores.
        kono_attestations: Number of independent sessions that attested
            this gene's value via KONO memory substrate.
        kono_permanent: Whether KONO has promoted this gene's memory
            to permanent status (3+ attestations).
        fitness_floor: Minimum fitness this gene can decay to.
            Set by: protected=True (0.8) or kono_permanent=True (0.6).
    """
    # ... all fields from Section 3.1 ...
    domain_fitness: DomainFitness = field(default_factory=DomainFitness)
    kono_attestations: int = 0
    kono_permanent: bool = False

    @property
    def fitness_floor(self) -> float:
        if self.protected:
            return 0.8
        if self.kono_permanent:
            return 0.6
        return 0.0

    @property
    def fitness(self) -> float:
        """Effective fitness: domain aggregate with KONO floor."""
        raw = self.domain_fitness.aggregate if self.domain_fitness.domain_scores else self._base_fitness
        return max(self.fitness_floor, raw)
```

## 19. LLM Attribution Layer

The DNA architecture's original principle was "No LLM in the fitness loop" (Section 4.4).
This was correct for the FITNESS COMPUTATION — survival-based fitness avoids the 91-attractor.

However, the system lacked a REVIEW mechanism: after a converge run, all invoked genes
received equal credit regardless of their actual causal contribution. This created
three gaps:

1. **No intelligent review**: flat +0.05 for all invoked genes.
2. **Gene granularity too coarse**: skills contain many behaviors but fitness treats them as atomic.
3. **No skill genesis**: the loop couldn't create new behaviors based on observed patterns.

### 19.1 Solution: LLM Attribution (not LLM Scoring)

The key insight: LLM attribution and LLM scoring are fundamentally different tasks.

- **Scoring** (banned): "How good is this output?" -> 91-attractor
- **Attribution** (added): "Which genes caused this outcome?" -> causal analysis

Attribution doesn't score quality. It assigns CREDIT. The mechanical fitness update
remains the ground truth; attribution is a MODIFIER that scales the delta.

### 19.2 ReviewVerdict

```python
@dataclass
class ReviewVerdict:
    attributions: dict[str, float]      # gene -> weight [0, 1], sum <= 1.0
    negative_attributions: dict[str, float]  # gene -> weight [-1, 0]
    proposed_skills: list[dict]         # micro-skill genesis proposals
    reasoning: str                      # one-line causal explanation
```

Weights are normalized: `sum(attributions.values()) <= 1.0`. This prevents
"everything was great" inflation — a constraint that forces genuine discrimination.

### 19.3 Conditional Review (~30% of runs)

Reviews are triggered by `should_review()`:

| Trigger | Condition | Why |
|---------|-----------|-----|
| Significant success | improvement > 15 | What worked? |
| Regression | improvement < -5 | What went wrong? |
| Max depth stall | not satisfied and depth >= 3 | What's missing? |
| Routine success | (skipped) | Mechanical fitness suffices |

Token cost: ~500 tokens per review, ~30% trigger rate = ~175 tokens/run average.

### 19.4 Weighted Fitness Update

```python
# Without attribution (backwards compatible):
delta = base_delta * 1.0  # full credit

# With attribution:
weight = verdict.attributions.get(gene.name, 1.0)
delta = base_delta * weight  # proportional credit
```

The mechanical update runs FIRST. Attribution then adjusts what was already applied.
If no review occurs, behavior is identical to pre-attribution versions.

### 19.5 Micro-Skill Genesis

When the review identifies a behavior pattern that succeeded but doesn't match any
existing gene, it proposes a micro-skill:

```python
proposal = {
    "name": "retry-with-backoff",
    "source": "code-mode",         # parent gene
    "behavior": "Apply exponential backoff on transient failures"
}
```

The micro-skill is:
- A real GRIP skill (3-10 line SKILL.md with YAML frontmatter)
- Enters the genome at fitness 0.5 with nursery protection (5 generations)
- Carries lineage: `genesis:{source_gene}`
- If consistently invoked and attributed -> thrives independently
- If never invoked -> decays and archives naturally

This produces **emergent granularity**: broad skills decompose into focused ones
through evolutionary pressure, not upfront restructuring. The system discovers
which behaviors within broad skills are worth extracting based on EVIDENCE.

### 19.6 Compounding: Attribution + Ablation

The two systems compound:

- **Ablation** (statistical): catches hitchhiker genes over ~20 runs
- **Attribution** (LLM): provides immediate causal discrimination

Over time they converge on the same answer — attribution front-runs what ablation
would eventually discover. Ablation validates attribution's accuracy.

### 19.7 Implementation Files

| Component | Location |
|-----------|----------|
| `ReviewVerdict` | `lib/converge_dna.py` |
| `should_review()` | `lib/converge_dna.py` |
| `build_review_prompt()` | `lib/converge_dna.py` |
| `parse_review_response()` | `lib/converge_dna.py` |
| `apply_review_verdict()` | `lib/converge_dna.py` |
| `propose_micro_skill()` | `lib/converge_dna.py` |
| `apply_genesis_proposals()` | `lib/converge_dna.py` |
| Protocol (Step 7) | `skills/converge/SKILL.md` |
| Verdict history | `cache/review-verdicts.jsonl` |

### 19.8 Migration

Phase 5 (LLM Attribution Layer) is additive:
- No changes to existing data structures (Gene, Genome, ConvergeOutcome)
- One optional parameter added to `evolve_generation()` (`verdict`)
- Fully backwards compatible: without a verdict, behavior is identical to Phase 3
- `should_review()` ensures reviews only trigger for informative runs

---

*Design: BROLY council, Gen N+1. 2026-02-20.*
*Augmented with KONO memory substrate, CUBE domain fitness, and LLM Attribution Layer.*
*Implementation: GRIP framework.*
