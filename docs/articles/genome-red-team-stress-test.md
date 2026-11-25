# Red-Teaming the GRIP Evolutionary Genome: Five Structural Vulnerabilities

*February 2026 | GRIP v6.0.0 | Gen 341*

---

## Abstract

The GRIP evolutionary genome (`converge_dna.py`) tracks 162+ genes representing skills, agents, rules, and modes. Each gene has a fitness value that evolves through converge() outcomes: invoked genes gain fitness on success, uninvoked genes decay. After 6 generations of live operation, we red-teamed the architecture and found six structural vulnerabilities — three critical, three important — that can corrupt the fitness signal, cause mass extinction events, and allow cross-project contamination.

This article documents each vulnerability with reproduction steps, proposes fixes ranked by impact, and establishes a testing protocol for validation.

---

## Vulnerability 1: Global Genome Path (CRITICAL)

### Finding

The genome is stored at a single global path with no project scoping:

```python
# converge_dna_core.py:22
GENOME_PATH = CLAUDE_DIR / "cache" / "genome.json"
```

No `project` or `project_path` field exists anywhere in the `Genome`, `Gene`, or `ConvergeOutcome` data structures. Every converge() run — regardless of which project it serves — reads and writes the same genome file.

### Reproduction

1. Open Session A on Project X (a Python API)
2. Open Session B on Project Y (a React frontend)
3. Session A runs `/converge fix auth middleware` → invokes genes: `code-mode`, `design-principles`, `python-async-safety`
4. Session B runs `/converge add dark mode` → invokes genes: `code-mode`, `ui-complete`, `mobile-responsive-ui`
5. `python-async-safety` gets `expressed_failure` penalty from Session B (expressed but not invoked in a frontend task)
6. `ui-complete` gets `expressed_failure` penalty from Session A (expressed but not invoked in a backend task)

### Impact

Genes accumulate fitness signals from irrelevant contexts. A skill perfectly suited for backend work gets penalized because it wasn't invoked during frontend tasks in a different project. Over many sessions across multiple projects, the fitness landscape converges toward genes useful everywhere (generalists) and away from specialists — the opposite of what evolution should produce.

### Proposed Fix: Dual-Layer Genome Architecture

**Layer 1: Project-scoped genome** (primary evolutionary layer):

```python
def get_genome_path(project_path: Optional[str] = None) -> Path:
    """Project-scoped genome, falling back to global."""
    if project_path:
        encoded = project_path.replace("/", "-").replace(".", "-")
        return CLAUDE_DIR / "projects" / encoded / "cache" / "genome.json"
    return GENOME_PATH  # Global fallback
```

**Layer 2: Global meta-genome** (read-only reference for universal patterns):

The global genome at `~/.claude/cache/genome.json` becomes a *meta-genome* that aggregates cross-project signals. When a gene achieves high fitness (>0.8) in 3+ distinct project genomes, it's promoted to the meta-genome. Project genomes inherit meta-genome baselines but evolve independently.

**Migration strategy**:

1. On first run after upgrade: copy current `genome.json` → `genome-global.json` (backup)
2. Create project-scoped genome for the current project, seeded from global
3. Future `bootstrap_genome()` calls check for project genome first, global second
4. Add `project_path` field to `ConvergeOutcome` (currently missing)
5. Backward compatible: sessions without project context continue using global genome

---

## Vulnerability 2: No File Locking on save_genome() (CRITICAL)

### Finding

```python
# converge_dna.py:486
target.write_text(json.dumps(data, indent=2, default=str))
```

No `fcntl.flock()`, no atomic write (write-to-temp + rename), no advisory locking. Two concurrent sessions can:

1. Session A reads genome at gen 5
2. Session B reads genome at gen 5
3. Session A evolves to gen 6, writes
4. Session B evolves to gen 6 (from stale gen 5), overwrites Session A's gen 6

Session A's evolution is silently lost.

### Evidence from Live Operation

The genome history shows generation jumps that suggest this already happened:

```
Gen 0 → Gen 1 (90b1a7dd7487)
Gen 1 → Gen 3 (5ce4b710f578)  # Gen 2 is missing
```

The jump from gen 1 to gen 3 with a `parent_id` pointing to `2b751d255b79` (which isn't gen 1's ID `90b1a7dd7487`) suggests a concurrent session forked from an intermediate state.

### Impact

Lost evolutionary progress. The genome's fitness history becomes unreliable because some generations are silently overwritten. In the worst case, a stale session can revert a well-evolved genome to a previous state.

### Proposed Fix

Atomic writes with advisory locking:

```python
import tempfile, fcntl

def save_genome(genome, path=None):
    target = path or GENOME_PATH
    lock_path = target.with_suffix('.lock')

    with open(lock_path, 'w') as lock_file:
        fcntl.flock(lock_file, fcntl.LOCK_EX)
        try:
            # Write to temp file first
            fd, tmp = tempfile.mkstemp(dir=target.parent, suffix='.json')
            os.write(fd, json.dumps(data, indent=2, default=str).encode())
            os.close(fd)
            os.rename(tmp, str(target))  # Atomic on POSIX
        finally:
            fcntl.flock(lock_file, fcntl.LOCK_UN)
```

---

## Vulnerability 3: Nursery Cliff Mass Extinction (CRITICAL)

### Finding

The genome uses two interacting mechanisms that combine catastrophically:

1. **Nursery protection** (`NURSERY_GENERATIONS = 5`): Genes younger than 5 generations skip time-based decay
2. **Time-based decay**: `fitness *= 0.95 ^ days_since_last_invocation`
3. **Default days**: When `last_invoked` is empty string (never invoked), defaults to `days = 30.0`

```python
# converge_dna_fitness.py:152
else:
    days = 30.0  # Never invoked → treated as 30 days stale

# converge_dna_fitness.py:154
decayed = gene._base_fitness * (DECAY_RATE ** days)
# 0.5 * 0.95^30 = 0.5 * 0.214 = 0.107 → below DEAD_THRESHOLD (0.2)
```

### Reproduction

1. Bootstrap genome: 162 genes, all `_base_fitness = 0.5`, `last_invoked = ""`
2. Run 5 converge() iterations (only 3-4 genes invoked per run)
3. At gen 6: nursery expires for all gen-0 genes simultaneously
4. Time-based decay applies: `0.5 * 0.95^30 = 0.107`
5. All 158+ uninvoked genes drop below `DEAD_THRESHOLD = 0.2`
6. Mass extinction: 120 of 163 genes die

### Evidence from Live Operation

The genome history proves this exact sequence:

```jsonl
// Gen 5, 21:26:07.417 — before decay
{"generation": 5, "avg_fitness": 0.4659, "living_count": 163, "dead_count": 0}

// Gen 5, 21:26:07.471 — after decay (same second!)
{"generation": 5, "avg_fitness": 0.6207, "living_count": 43, "dead_count": 120}
```

120 genes died in 54 milliseconds. The average fitness *increased* because the dead genes were excluded from the average. This is survivorship bias in the metric itself — the genome looks "healthier" precisely because most of its genes just died.

### Impact

A 73.6% gene extinction rate on first nursery expiration. Skills that haven't been invoked in the first 5 converge runs are permanently killed, regardless of their actual utility. Skills suited for infrequent tasks (deployment, security auditing, legal review) are disproportionately affected because they're less likely to be invoked in the first 5 runs.

### Proposed Fix

Two changes:

1. **Bootstrap `last_invoked` to creation time**, not empty string:
   ```python
   gene.last_invoked = datetime.now(timezone.utc).isoformat()
   ```

2. **Graduated nursery exit** instead of cliff:
   ```python
   # Instead of binary nursery on/off at gen 5:
   nursery_factor = min(1.0, age_in_generations / NURSERY_GENERATIONS)
   effective_decay = DECAY_RATE ** (days * nursery_factor)
   ```

---

## Vulnerability 4: InvocationRegistry Singleton (IMPORTANT)

### Finding

```python
# converge_dna_core.py:90-92
# Module-level singleton — NOT thread-safe. Concurrent callers (parallel tests,
# async) will mix results. Acceptable for single-threaded CLI use.
_registry = InvocationRegistry()
```

The comment acknowledges the limitation but frames it as acceptable. In practice, GRIP spawns parallel agents (broly mesh, team workers) that may import the same module and share the singleton.

### Impact

Invocation tracking becomes unreliable when:
- Broly mesh council spawns 3-6 parallel agents
- Team mode has multiple workers sharing the same `.claude` directory
- Background agents (copilot workflows) run alongside interactive sessions

Wrong invocation data → wrong fitness updates → genome drift from reality.

### Proposed Fix

Context-scoped registry (e.g., pass registry as parameter, or use `contextvars` for async safety):

```python
import contextvars
_registry_var: contextvars.ContextVar[InvocationRegistry] = contextvars.ContextVar('registry')
```

---

## Vulnerability 5: Disconnected Memory Systems (IMPORTANT)

### Finding

GRIP has four memory/learning systems that should form a coherent feedback loop but are largely disconnected:

| System | What It Tracks | Genome Integration |
|--------|---------------|-------------------|
| **Genome** (`converge_dna.py`) | Gene fitness via converge() outcomes | Self (core) |
| **KONO** (`kono_substrate.py`) | Semantic memories in SQLite | Minimal — only displays fitness in status line (line 1654). `apply_kono_attestation()` exists but no automatic pipeline feeds attestations. |
| **CUBE** (`converge_dna_core.py`) | Per-domain fitness (DRY, SOLID, KISS, YAGNI, GRASP, efficiency) | Optional — only when `cube_scores` explicitly passed in ConvergeOutcome. No automatic scoring. |
| **Tasks API** (`CLAUDE_CODE_TASK_LIST_ID=grip`) | Native Claude Code task tracking | None — task completion signals don't feed into genome fitness at all. |

### The Missing Feedback Loops (with concrete wiring proposals)

**1. KONO → Genome** (most impactful gap):

The function `apply_kono_attestation(gene, attestation_count)` exists in `converge_dna_fitness.py:158` but nothing calls it automatically. KONO's `consolidate()` method marks embeddings as permanent when they reach 3+ attestations, but doesn't notify the genome.

```python
# Proposed: Add to kono_substrate.py consolidate() method (line 863)
def consolidate(self) -> int:
    count = ...  # existing consolidation logic

    # NEW: Feed permanence signals to genome
    if count > 0:
        try:
            from lib.converge_dna import load_genome, save_genome
            from lib.converge_dna_fitness import apply_kono_attestation
            genome = load_genome()
            if genome:
                # Map permanent embedding content to gene names
                for emb_id in newly_permanent_ids:
                    gene_name = self._embedding_to_gene(emb_id)
                    if gene_name and gene_name in genome.genes:
                        att_count = self._get_attestation_count(emb_id)
                        apply_kono_attestation(genome.genes[gene_name], att_count)
                save_genome(genome)
        except ImportError:
            pass
    return count
```

**2. Tasks API → Genome**:

The `TaskCompleted` hook (`anti-drift-hook.py`) fires on every task completion but only outputs a "What's Up Next" reminder. It should also record which genes were active during the completed task.

```python
# Proposed: Add to anti-drift-hook.py or new task-genome-bridge.py
# Triggered by TaskCompleted hook event
def record_task_completion_to_genome():
    """Feed task completion signal into genome fitness."""
    genome = load_genome()
    if not genome:
        return

    # The expressed genes for this session are tracked by InvocationRegistry
    invoked = _registry.get_invoked()
    for gene_name in invoked:
        if gene_name in genome.genes:
            gene = genome.genes[gene_name]
            gene._base_fitness = min(1.0, gene._base_fitness + 0.01)
    save_genome(genome)
```

**3. CUBE → Genome** (partially wired, needs automation):

`update_gene_domain_fitness()` already updates per-domain fitness when `cube_scores` are passed in `ConvergeOutcome`. The gap is that `cube_scores` must be manually constructed. Proposal: auto-extract CUBE scores from the `principles-criterion` skill when it's invoked during converge().

**4. Genome → KONO** (reverse direction):

Gene fitness should boost KONO memory retrieval. In `kono_substrate.py`'s retrieval scoring, add a `gene_fitness_boost` factor:

```python
# In recall/search, after computing base score:
gene_name = self._embedding_to_gene(emb_id)
if gene_name and genome and gene_name in genome.genes:
    gene_fitness = genome.genes[gene_name].fitness
    score *= (0.5 + 0.5 * gene_fitness)  # High-fitness genes boost recall
```

### Impact

The genome evolves in isolation from GRIP's broader learning systems. A skill could accumulate 50 KONO attestations (proving its utility through repeated memory access) while its genome fitness decays to zero because it wasn't explicitly invoked in converge() runs. The systems can diverge, giving contradictory signals about what's valuable.

### Integration Map (Current vs Proposed)

```
Current:
  Genome ← converge() outcomes (only source of fitness)
  KONO   ← session memories (independent fitness decay)
  CUBE   ← manual dimension scores (when remembered)
  Tasks  ← native Claude Code tracking (no genome connection)

Proposed:
  Genome ← converge() outcomes + KONO attestations + task completions
  KONO   ← session memories + genome fitness boost on retrieval
  CUBE   ← auto-extracted from principles-criterion → genome domain fitness
  Tasks  ← native tracking → genome positive signal on completion
```

---

## Vulnerability 6: Session-Start Decay Amplification (IMPORTANT)

### Finding

Every session start that calls `bootstrap-or-load` applies time-based decay and saves:

```python
# converge_dna.py:1058-1062
elif cmd == "bootstrap-or-load":
    genome = load_genome()
    if genome:
        apply_time_decay(genome)  # Decay applied on EVERY load
        save_genome(genome)       # And saved immediately
```

While nursery protection (`NURSERY_GENERATIONS = 5`) prevents decay during the nursery period, once genes graduate (gen 5+), each session start applies `0.95^days` decay. If a user opens 5 sessions in one day, decay is applied 5 times — not once per day as intended.

### Evidence

The genome history shows 4 `bootstrap-or-load` calls at gen 0 within 4 minutes (20:56 to 21:00). Nursery protection held during these calls (avg stayed near 0.53). But once nursery expires at gen 5+, this same pattern would compound: 5 sessions × decay = 5× the intended daily decay rate.

### Impact

Users who work in multiple concurrent sessions or restart frequently accelerate gene death. The time-based decay was designed as `0.95^days_since_invocation` (daily), but the implementation makes it `0.95^(days × session_starts)` (per-session-start). This violates the decay model's assumptions.

### Proposed Fix

Add a last-decay timestamp to prevent repeated application:

```python
def apply_time_decay(genome: Genome) -> None:
    """Apply time decay at most once per calendar day."""
    last_decay = genome.metadata.get("last_decay_applied")
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if last_decay == today:
        return  # Already decayed today
    # ... existing decay logic ...
    genome.metadata["last_decay_applied"] = today
```

---

## TODO Audit (Gen 341 Status Check)

| # | Item | Previous Status | Verified Status | Evidence |
|---|------|----------------|-----------------|----------|
| 107 | Fix MCP Apps: revert dual-content-block, use EmbeddedResource | PENDING | **DONE** | `artifact-bridge.ts` lines 253-264 implement EmbeddedResource (`type: "resource"`, `mimeType: "text/html"`). Commit `e8f0137` applied the fix. |
| 108 | Post SchipOTP PR #4 review to GitHub | PENDING | **STILL PENDING** | Awaiting personal note from user before posting. Code review complete. |
| 110 | Vite singlefile audit for MCP Apps | PENDING | **STILL PENDING** | No evidence of resolution. 314-328KB bundle size still applies. |
| 111 | Clean up 38 orphaned UUID task dirs | PENDING | **STILL PENDING** | `~/.claude/tasks/` directories not yet cleaned. Low priority. |
| 112 | Audit MEMORY.md write conflict (3 silos) | PENDING | **PARTIALLY RESOLVED** | PR #246 fixed Broly LTM wrong path. Native auto-memory and KONO still write independently. Coexistence not formally verified. |
| 113 | Anchor GRIP instances to native session UUIDs | PENDING | **STILL PENDING** | No implementation started. |

---

## Recommendations (Ranked by Impact)

### P0: Project-Scoped Genome (Vulnerability 1)

**Effort**: Medium (path change + migration script)
**Impact**: Eliminates cross-project contamination entirely
**Risk**: Specialist skills may not get enough data in small projects

### P0: Atomic save_genome() with File Locking (Vulnerability 2)

**Effort**: Small (~20 lines)
**Impact**: Prevents silent data loss from concurrent sessions
**Risk**: Lock contention on slow filesystems (mitigate with timeout)

### P1: Fix Bootstrap last_invoked + Graduated Nursery (Vulnerability 3)

**Effort**: Small (2 changes, ~10 lines each)
**Impact**: Prevents 73.6% mass extinction on nursery expiration
**Risk**: Slower convergence toward "useful" genes (acceptable tradeoff)

### P1: KONO → Genome Attestation Pipeline (Vulnerability 5)

**Effort**: Medium (wire `apply_kono_attestation()` into KONO's permanence check)
**Impact**: Connects two learning systems, prevents contradictory fitness signals
**Risk**: Double-counting if attestation and invocation overlap

### P2: Daily Decay Dedup (Vulnerability 6)

**Effort**: Tiny (~10 lines, add metadata timestamp check)
**Impact**: Prevents multi-session decay amplification
**Risk**: None — strictly defensive, no behavior change for single-session users

### P2: Context-Scoped InvocationRegistry (Vulnerability 4)

**Effort**: Small (use `contextvars`)
**Impact**: Correct tracking in parallel agent scenarios
**Risk**: Requires testing with actual parallel spawns

---

## Test Plan (Verification Protocol)

### Unit Tests (per vulnerability)

| Test | Validates | Method |
|------|-----------|--------|
| `test_project_scoped_genome_isolation` | Vuln 1 | Two genomes at different project paths, evolve independently, assert no cross-contamination |
| `test_concurrent_save_no_data_loss` | Vuln 2 | Two threads read→evolve→save same genome, assert both generations present in history |
| `test_nursery_graduated_exit` | Vuln 3 | Bootstrap genome, run 10 generations with 3 invoked genes, assert >50% survival rate (vs current 26.4%) |
| `test_registry_context_isolation` | Vuln 4 | Two contextvars registries in parallel coroutines, assert no invocation mixing |
| `test_kono_attestation_feeds_genome` | Vuln 5 | Create permanent KONO memory, assert corresponding gene fitness increased |
| `test_daily_decay_dedup` | Vuln 6 | Call `apply_time_decay` 5 times same day, assert fitness only decayed once |

### Integration Tests

| Test | Validates | Method |
|------|-----------|--------|
| `test_full_converge_with_genome_evolution` | End-to-end | Run converge(), verify genome.generation incremented, living genes updated, metrics recorded |
| `test_cross_project_converge_isolation` | Vuln 1 + 2 | Start two converge() runs in different project dirs, verify separate genome files |
| `test_task_completion_feeds_genome` | Vuln 5 | Simulate `TaskCompleted` hook, verify invoked genes get positive fitness delta |

### Smoke Test (manual)

```bash
# Run after implementing fixes:
python3 ~/.claude/lib/converge_dna.py bootstrap-or-load
python3 ~/.claude/lib/converge_dna.py show | head -20
# Expect: all genes living, avg_fitness ~0.5, no mass extinction
```

---

## Falsification Review

**Hypothesis confirmed**: 6 vulnerabilities found, all with concrete reproduction steps:
- Vuln 1: Reproduction via two concurrent projects (structural, code inspection)
- Vuln 2: Reproduction via two concurrent sessions (structural + history evidence of missing gen 2)
- Vuln 3: Reproduction via 6 generations (empirically observed: 120/163 genes died in genome-history.jsonl)
- Vuln 4: Reproduction via parallel agent import (structural, code inspection + comment on line 90-91)
- Vuln 5: Reproduction via code inspection (missing function calls between 4 memory systems)
- Vuln 6: Reproduction via multiple session starts (structural: `bootstrap-or-load` applies decay + saves every time)

**Null hypothesis rejected**: Existing safeguards (nursery, KONO attestation, protected genes) are insufficient. Nursery *causes* the mass extinction by creating a cliff (Vuln 3). KONO attestation exists in code but has no automatic trigger (Vuln 5). Protected genes survive but represent <10% of the gene pool. Session-start decay is applied repeatedly without deduplication (Vuln 6).

**Pattern Check**:
- Over-mapping: No. Each vulnerability has specific code references, not analogies.
- Feedback loop: Genome history data is objective (timestamps, counts). Not self-scored.
- Fluency penalty: Not applicable — this is a vulnerability report, not iterative prose.

---

## Appendix: Genome History Timeline

```
20:56:07  Gen 0  Bootstrap: 162 genes, avg 0.5333, all living
20:59:10  Gen 0  Enrichment: avg 0.5377 (slight bump from usage data)
21:00:26  Gen 0  Unknown mutation: avg drops to 0.4175
21:01:47  Gen 1  First evolution: avg 0.4213, success_rate 1.0
21:06:02  Gen 1  Second snapshot: avg 0.4366
21:06:46  Gen 3  Third evolution: avg 0.4634 (gen 2 missing — concurrent session?)
21:07:17  Gen 4  Fourth: avg 0.4646, gene_count 163 (+1 genesis)
21:07:17  Gen 5  Fifth: avg 0.4659
21:26:07  Gen 5  MASS EXTINCTION: 120 genes die, avg jumps to 0.6207 (survivorship bias)
21:26:34  Gen 5  Post-extinction adjustment: avg 0.6002
21:27:32  Gen 5  Further decay: avg 0.5958
21:34:56  Gen 6  Current: avg 0.6043, 43 living, 120 dead
```

---

### Gen 0 Fitness Drop (Under Investigation)

Between timestamps 20:59 (avg 0.5377) and 21:00 (avg 0.4175), the genome experienced a 22% fitness drop while still at gen 0. Nursery protection should have prevented time-based decay at gen 0 (`age_in_generations = 0 < 5`). Possible causes:

1. A concurrent session called `sync_with_evolutionary_refactor` which updated `last_invoked` dates, triggering decay on a separate `apply_time_decay` call in the same minute
2. An external process modified the genome file directly
3. A code path applies decay without nursery checks

This requires further investigation with debug logging enabled.

---

*Produced by converge() red-team analysis, Gen 341. Evidence: genome.json, genome-history.jsonl, converge_dna_core.py, converge_dna_fitness.py, converge_dna.py, kono_substrate.py, artifact-bridge.ts, settings.json, anti-drift-hook.py.*
