---
name: converge
description: Universal convergence loop for any task. Iteratively improves output until criterion met or max_depth reached. The command IS the pattern it implements.
status: Active
effort: high
version: 1.0.0
triggers:
  - "/converge"
  - "converge on"
  - "iterate until good"
  - "keep improving"
integrates:
  - converge.py
  - converge_combinators.py
  - converge_inherit.py
  - converge_metrics.py
  - converge_dna.py
see_also:
  - rsi-aliases
  - prompt-coach
  - design-principles
  - principles-criterion
  - self-improvement-engine
  - hypothesis-validation
  - broly-meta-agent
---

# /converge — Universal Convergence Command

## Live Context (DCI)

**Convergence metrics:** !`cd ~/.claude && tail -1 learning/converge_metrics.jsonl 2>/dev/null | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'depth={d.get(\"depth\",\"?\")}/{d.get(\"max_depth\",\"?\")} met={d.get(\"criterion_met\",\"?\")}')" 2>/dev/null || echo "no convergence data"`
**DELIGHT v2:** !`cd ~/.claude && tail -1 learning/delight_v2.jsonl 2>/dev/null | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'composite={d[\"composite\"]:.3f} efficiency={d.get(\"convergence_efficiency\",0):.2f}')" 2>/dev/null || echo "no data"`
**Git state:** !`cd ~/.claude && git log --oneline -1 2>/dev/null`
**Hypotheses:** !`cd ~/.claude && PYTHONPATH=. python3 -c "from lib.hypothesis_engine import status; s=status(); print(f'{s[\"total\"]} total, rate={s[\"confirmation_rate\"]:.0%}')" 2>/dev/null || echo "no data"`

```
converge(state, criterion, improve, max_depth=3, time_budget_sec=None)
```

The agent becomes `improve()`. The task output is `state`. Quality assessment is `criterion`. Recursion does the rest.

## Live Context (DCI)

**Git branch:** !`cd ~/.claude && git branch --show-current 2>/dev/null`
**Last commit:** !`cd ~/.claude && git log --oneline -1 2>/dev/null`
**RSI active:** !`test -f ~/.claude/state/rsi/rsi-active.lock && echo "YES — converge within RSI loop" || echo "standalone"`
**Open PRs:** !`cd ~/.claude && gh pr list --limit 3 --json number,title --jq 'length' 2>/dev/null || echo "?"` open

## Protocol

### Step 0: Parse

#### Semantic Intent Matching (preferred)

Type what you want. GRIP detects intent from natural language — no flags needed.

**Intent detection (keyword → task type)**

| Keywords | Auto-detected type | Example input |
|---|---|---|
| fix, refactor, test, implement, build | code | `fix the auth middleware` |
| explain, teach, describe, clarify, summarize | explanation | `explain recursion simply` |
| design, architect, schema, plan, structure | design | `design the notification API` |
| audit, investigate, review, analyze, inspect | analysis | `audit the payment flow` |
| write, draft, brainstorm, create, compose | creative | `brainstorm names for the feature` |
| (none match) | general | `improve the onboarding` |

**Positional depth** — `N:` anywhere in input sets max_depth=N:

```
/converge 5: fix the auth middleware    → depth=5, type=code
/converge 2: explain recursion          → depth=2, type=explanation
```

**Criterion shorthand** — `>N` sets a criterion threshold of N:

```
/converge >85 design the schema         → criterion threshold 85, type=design
```

**Compound patterns** (all combinable):

```
/converge 5: fix the auth middleware    → depth 5, code
/converge 4: >90 design the API schema  → depth 4, threshold 90, design
/converge audit the payment flow        → depth 3 (default), analysis
```

vs explicit flags (same result, ~55% more characters):

```
/converge --depth 5 --code fix the auth middleware
/converge --depth 4 --criterion ">90" --code design the API schema
/converge --code audit the payment flow
```

#### Flag reference (backward compatible)

All explicit flags still work and take precedence over inferred values:

- `task`: everything after `/converge` (excluding flags and prefix patterns)
- `--depth N`: override max_depth (default: 3)
- `--criterion "text"`: override criterion (default: auto-detect)
- `--show-work`: emit scoring details at each depth
- `--code`: wire to actual `converge()` Python function for code tasks

#### Parse precedence

1. Explicit flags (`--depth`, `--criterion`, `--code`) — highest priority
2. Positional depth (`N:`) and threshold (`>N`) patterns
3. Semantic intent detection from keywords
4. Defaults (depth=3, type=general)

### Step 0.5: Falsification Pre-Registration

Before the loop begins, state explicitly:

1. **Hypothesis**: What specific claim will this converge run produce or validate?
2. **Null hypothesis**: What would be true if the improvement is illusory?
3. **Falsification criteria**: What specific evidence would disprove the output?

This prevents the feedback loop where fluent-sounding output self-validates.
If you cannot state what would falsify the output, the task is not well-defined.

> "The Pattern That Wasn't There" — LLMs produce text resembling discovery
> without performing the epistemic work that validates it. Pre-registration
> forces the epistemic work to happen before the pattern-matching begins.

For simple tasks (fix a bug, explain a concept), pre-registration can be one line:
*"Hypothesis: the fix resolves the error. Falsified if: tests still fail."*

For analysis/design tasks, it must be explicit and specific.

### Step 1: Classify Task Type

Determines scoring rubric:

| Type | Detection | Criterion Weights |
|------|-----------|-------------------|
| code | mentions files, functions, tests, refactor | correctness 40%, design 30%, simplicity 30% |
| explanation | explain, simplify, teach, describe | clarity 40%, accuracy 30%, brevity 30% |
| design | design, architect, API, schema | completeness 40%, coherence 30%, elegance 30% |
| analysis | analyze, investigate, audit, review | evidence 40%, insight 30%, actionability 30% |
| creative | write, draft, brainstorm, name | originality 40%, fitness 30%, polish 30% |
| general | (fallback) | quality 40%, completeness 30%, conciseness 30% |

### Step 2: Execute Convergence Loop

```python
# This is what the agent does — not code to run, but behavior to follow
depth = 0
best_output = None
best_score = 0

while depth <= max_depth:
    # PRODUCE: Generate or refine the output
    if depth == 0:
        output = execute_task(task)
    else:
        output = improve(previous_output, weaknesses)

    # SCORE: Evaluate per-dimension, compute composite (0-100)
    dimension_scores = evaluate_dimensions(output, task_type)
    score = compute_composite(dimension_scores, task_type, weaknesses)

    # TRACK BEST: Keep highest-scoring output (not just latest)
    if score > best_score:
        best_output = output
        best_score = score

    # CRITERION CHECK: Stop if good enough
    if score >= 93:
        break

    # IDENTIFY WEAKNESSES: What specifically needs improving?
    weaknesses = identify_weaknesses(output, score, criterion)

    # VELOCITY CHECK: If score didn't improve, switch strategy
    if depth >= 2 and score <= previous_score:
        switch_strategy()  # Try a fundamentally different approach

    previous_output = output
    previous_score = score
    depth += 1
```

#### Dynamic Depth (auto)
When no explicit `--depth` or `N:` prefix is given, consult history:
- `suggest_max_depth(task_type, task_hint=task_description)` from `converge_metrics.py`
- If task contains "big", "massive", "complex", "epic": allow up to 6
- If last 3 runs of same type converged at depth <= 2: cap at 3 (fast converger)
- If last 3 runs stalled (velocity <= 0 at depth 3): allow up to 5 (needs room)
- Default: 4 for code/analysis (step 1->2 yields +11.7 avg improvement), 3 for others
- User-specified depth ALWAYS takes precedence
- In `--show-work` mode, log the depth suggestion rationale

**CLI usage**:
```bash
python3 lib/converge_metrics.py suggest-depth code "implement a big auth refactor"
# Output: Suggested max_depth for 'code': 6
```

### Step 2.5: JIT Sub-Task Detection

During `improve()` at any depth, if the improvement requires a distinct sub-task:

**Detection heuristic**: The agent recognizes phrases like:
- "first I need to..." / "this requires..." / "blocked by..."
- A distinct sub-problem that has its own convergence criterion

**Action**: Spawn a mini-converge:
```
converge(sub_task, quick_criterion, improve, max_depth=2)
```
Fold the result back into the main loop as input for the next improvement.

**Caps** (prevent recursion explosion):
- Max 2 JIT sub-loops per depth
- Max depth 2 for sub-loops (no sub-sub-loops)
- Sub-loops inherit parent's task_type unless clearly different

**Genome tracking**: Each JIT sub-loop registers gene invocations via the parent run's
`invoked_genes` list. Sub-loops do NOT trigger separate genome evolution — they fold
into the parent outcome.

**Example**:
- Main: `/converge fix the auth middleware`
- Depth 1: "I need to understand the token refresh flow first"
- JIT: `converge("explain token refresh flow", clarity >= 80, improve, max_depth=2)`
- Result feeds back into main depth 2 improvement

### Step 3: Output Format

Each depth emits:

```markdown
## Depth {N}/{max_depth} | Score: {score}/100

{output}

---
*Weaknesses: {list}*
*Strategy: {what improve() will do next}*
```

Final output:

```markdown
## Converged | Depth {N}/{max_depth} | Score: {best_score}/100 | {satisfied ? "Criterion met" : "Max depth reached"}

{best_output}
```

If `--show-work` is set, show all depths. Otherwise, only show the final converged output.

### Step 3.5: Propose Commit + Push (MANDATORY for code/design tasks)

When a converge loop produces code deliverables (files modified, tests added, config changed),
propose committing and pushing at completion. Do not silently finish — the user expects
deliverables to land in the repo.

1. Stage only the files changed by this converge run (not unrelated dirty state)
2. Commit with a conventional commit message describing the converged output
3. Ask user: push to main (if small/infra) or create PR (if substantial/review-worthy)
4. Use the push hook bypass pattern if direct-to-main is blocked (feat branch + PR + merge)

This step applies to all task types that produce file changes. Explanation-only or
analysis-only converge runs (no file output) skip this step.

### Step 4: Record Strategy (for inheritance)

After convergence, if the task improved significantly (score delta > 20):

```python
# Record what worked for future /converge calls
from lib.converge_inherit import record_success
record_success(
    project=current_project,
    domain=task_type,
    strategy=winning_strategy_description,
    score_before=initial_score,
    score_after=best_score,
)
```

#### Insights Pipeline Trigger

If score delta > 15 (significant improvement), trigger insights pipeline stage 1 (mine):
- Capture the improvement pattern for cross-session learning
- Pattern gets INS-0X identifier for tracking through the 7-stage pipeline
- Insights that survive red-team (stage 4) become micro-skill genome proposals via `apply_genesis_proposals()`

### Step 5: Record Metrics (MANDATORY)

Every `/converge` invocation MUST record metrics for compound analysis.

At loop start — load genome and pass to metrics:
```python
from lib.converge_metrics import record_start, record_depth, record_end
from lib.converge_dna import load_genome, evolve_generation, save_genome, save_outcome, ConvergeOutcome
import time

genome = load_genome()  # Returns None if no genome exists yet
expressed_genes = [g.name for g in genome.express()] if genome else []

run_id = record_start(
    task=task_description, task_type=classified_type, max_depth=max_depth,
    genome_id=genome.id if genome else "",
    expressed_genes=expressed_genes,
)
start_time = time.time()
```

At each depth (dimension_scores is MANDATORY -- omitting it triggers a deprecation warning):
```python
record_depth(run_id, depth=N,
             dimension_scores={"correctness": 85, "design": 70, "simplicity": 90},
             weaknesses=weakness_list,
             strategy=strategy_description, deliverables=["commit:abc123"])
# The 'score' parameter is DEPRECATED. Composite is computed from dimension_scores
# via compute_composite_score(). Omitting dimension_scores causes the 91-attractor.
```

At loop end — record metrics AND evolve genome (dimension_scores MANDATORY):
```python
# Track which skills/tools were actually invoked during the loop
invoked_genes = [name for name in expressed_genes if name_was_used_during_loop(name)]

record_end(run_id, satisfied=bool,
           depth_reached=N, elapsed_minutes=wall_clock_minutes,
           commits=commit_count, velocity_trajectory=[45, 72, 91],
           dimension_scores={"correctness": 95, "design": 85, "simplicity": 90},
           invoked_genes=invoked_genes)
# The 'final_score' parameter is DEPRECATED. Pass dimension_scores instead.
```

### Step 6: Evolve Genome (MANDATORY when genome exists)

After recording metrics, feed the outcome back into the evolutionary DNA layer:

```python
if genome:
    outcome = ConvergeOutcome(
        run_id=run_id,
        genome_id=genome.id,
        generation=genome.generation,
        expressed_genes=expressed_genes,
        invoked_genes=invoked_genes,
        satisfied=satisfied,
        score_before=initial_score,
        score_after=best_score,
        depth_reached=depth_reached,
        elapsed_sec=time.time() - start_time,
        cube_scores=final_dimension_scores,  # Pass dimension scores as CUBE fitness
    )
    genome = evolve_generation(genome, outcome)
    save_genome(genome)
    save_outcome(outcome)
```

This closes the evolutionary feedback loop: each `/converge` run updates gene fitness
based on which skills were active and whether the run succeeded. Over many sessions,
useful skills gain fitness and get prioritized; unused skills decay; harmful patterns
are selected against.

### Step 7: LLM Attribution Review (CONDITIONAL)

After evolving the genome, check if this run warrants an LLM attribution review.
Reviews provide **causal attribution** (which genes drove the outcome), NOT quality
scoring (avoids the 91-attractor).

Reviews are conditional (~30% of runs) to control token cost:

```python
from lib.converge_dna import (
    should_review, build_review_prompt, parse_review_response,
    ReviewVerdict, apply_review_verdict, apply_genesis_proposals,
    save_verdict, save_genome,
)

if genome and should_review(outcome):
    # The agent IS the LLM reviewer. Perform attribution:
    # 1. Which invoked genes CAUSED the outcome? (not: how good was it?)
    # 2. Did any behavior emerge that isn't captured by existing genes?
    #
    # Build the structured prompt for reference:
    # prompt = build_review_prompt(outcome, invoked_genes)
    #
    # Then construct the verdict directly:
    verdict = ReviewVerdict(
        attributions={
            # gene_name: weight [0.0, 1.0], must sum to <= 1.0
            # Higher = more causal credit for the outcome
            "design-principles": 0.5,
            "code-mode": 0.3,
            "converge": 0.2,
        },
        negative_attributions={
            # gene_name: weight [-1.0, 0.0] for genes that hurt
        },
        proposed_skills=[
            # Micro-skill genesis proposals (optional):
            # {"name": "retry-with-backoff", "source": "code-mode",
            #  "behavior": "Apply exponential backoff on transient failures"}
        ],
        reasoning="one sentence explaining the causal chain",
        run_id=run_id,
    )
    verdict.normalize()  # Enforce weight constraints

    # Apply attribution weights on top of mechanical fitness
    genome = apply_review_verdict(genome, outcome, verdict)
    save_verdict(verdict)

    # Process genesis proposals (creates micro-skill files + genes)
    genome, created_paths = apply_genesis_proposals(genome, verdict)

    # Re-save genome with attribution adjustments
    save_genome(genome)
```

**When to trigger review** (from `should_review()`):
- Score improvement > 15 points (significant success)
- Score regressed < -5 points (failure analysis)
- Max depth reached without satisfying (stall analysis)
- Routine successes: SKIP (pure mechanical fitness is sufficient)

**Anti-91-attractor**: The review asks "which genes CAUSED this?" not "how good was it?"
Attribution weights must sum to <= 1.0 (enforced by `normalize()`).

**Micro-Skill Genesis**: When the review identifies a useful behavior not captured by
any existing gene, propose it as a micro-skill. The skill enters the genome at fitness
0.5 with nursery protection. Over time, broad skills naturally decompose into focused
ones through this evolutionary pressure (emergent granularity).

Analysis commands:
```bash
python3 ~/.claude/lib/converge_metrics.py summary    # All runs
python3 ~/.claude/lib/converge_metrics.py compound   # Compound proof
python3 ~/.claude/lib/converge_metrics.py last        # Last run details
```

## Scoring Rubric

### Per-Dimension Scoring (MANDATORY)

At every depth, score EACH dimension independently (0-100) using the task-type weight table.
The composite score is computed programmatically by `compute_composite_score()`.

**You MUST NOT assign a single overall score.** Instead, evaluate each dimension separately:

```python
# Code task example:
dimension_scores = {
    "correctness": 95,  # Does it work? Tests pass?
    "design": 70,       # Clean architecture? SOLID?
    "simplicity": 85,   # Minimal complexity? Easy to understand?
}
# Composite = 95*0.4 + 70*0.3 + 85*0.3 = 38 + 21 + 25.5 = 84.5
# Calibration v2: -3 per weakness, spread penalty if stdev < 5
```

Dimension names MUST match the weight table for your task type:

| Type | Dim 1 (40%) | Dim 2 (30%) | Dim 3 (30%) |
|------|-------------|-------------|-------------|
| code | correctness | design | simplicity |
| explanation | clarity | accuracy | brevity |
| design | completeness | coherence | elegance |
| analysis | evidence | insight | actionability |
| creative | originality | fitness | polish |
| general | quality | completeness | conciseness |

### Calibration (automatic)

`compute_composite_score()` v2 applies these adjustments:
- **Weakness penalty**: -3 per weakness (max -15)
- **Spread penalty**: gradient-based, penalizes dimension stdev < 5 (near-uniform = self-bias)
- **Deliverable bonus**: REMOVED (commits are milestones, not quality indicators)

v1 compressed all converged runs into an 8-point band (87-92). v2 produces
meaningful differentiation: genuinely spread dimensions score higher than
near-uniform ones at the same base.

### Score Calibration

| Score | Meaning | Action |
|-------|---------|--------|
| 93-100 | Criterion met. Output is excellent. | STOP. Return. |
| 70-92 | Good but has specific fixable weaknesses. | Improve targeted areas. |
| 50-69 | Mediocre. Structural issues. | Consider different approach. |
| 0-49 | Poor. Fundamental rethink needed. | Switch strategy entirely. |

### Honesty Gate

Before scoring each dimension, ask: "If I showed this to the user and they saw this dimension score, would they agree?"

If NO: adjust the specific dimension down. The user is the real criterion function.

### Pattern Check (The Pattern That Wasn't There)

At each depth, after scoring but before accepting the output, apply three checks:

**1. Over-Mapping Check** (Gentner's structural mapping)
If the output draws an analogy or theoretical connection, ask:
- "Do the *relations between elements* map, or just the elements themselves?"
- Surface analogy (DNA ↔ code, convergence ↔ golden ratio) is not evidence.
- Only structural correspondence (same causal mechanisms) counts.

**2. Feedback Loop Check**
- "Am I scoring this highly because it *sounds like* an improvement, or because I can point to specific, falsifiable evidence of improvement?"
- If the evidence is "it reads better" or "it feels more complete" — that is the feedback loop. Score down.
- Demand concrete evidence: test results, measurable metrics, specific user-visible changes.

**3. Fluency Penalty**
- If the output is more *eloquent* than the previous depth but not more *correct*, apply -5 to the relevant dimension.
- LLMs excel at making each iteration sound more polished. Polish without substance is the failure mode the article warns about.

> Case study: The phi-proximity claim (run 7c9a6092) passed every check
> EXCEPT the over-mapping check. L ≈ phi *sounded* like a deep structural
> connection between Banach's theorem and LLM improvement. A geometric model
> with R^2=0.998 showed it was surface analogy only.

### Anti-Attractor Rule

If you find yourself scoring all dimensions the same (e.g., 91/91/91), STOP.
This is the attractor pattern. Each dimension measures something different —
they should produce different numbers. The uniformity penalty exists for this reason.

## Velocity & Stall Detection

Track score trajectory across depths:

```
velocity = score[depth] - score[depth-1]
```

| Velocity | Meaning | Action |
|----------|---------|--------|
| > 10 | Accelerating. Approach is working. | Continue same strategy. |
| 1-10 | Slow improvement. | Consider refinement. |
| 0 | Stalled. Same approach won't help. | Switch strategy. |
| < 0 | Regressing. Last change made it worse. | Revert to best_state, try different angle. |

## --code Mode: Wiring to converge()

When `--code` flag is present and the task involves actual code:

```python
import sys
sys.path.insert(0, str(Path.home() / ".claude" / "lib"))
from converge_metrics import converge_tracked
from converge_combinators import all_of, threshold, pipeline, VelocityTracker
from converge_dna import load_genome, evolve_generation, save_genome, save_outcome, ConvergeOutcome

# Load genome for DNA tracking
genome = load_genome()

# Wire the actual kernel with metrics + genome tracking
result = converge_tracked(
    state=current_code_state,
    criterion=all_of(
        threshold("tests_passing", 95),
        threshold("design_score", 80),
    ),
    improve=agent_improvement_cycle,
    max_depth=depth_from_args,
    _scorer=composite_scorer({"tests_passing": 0.4, "design_score": 0.3, "simplicity": 0.3}),
    label=f"converge-{task_slug}",
    genome_id=genome.id if genome else "",
    expressed_genes=[g.name for g in genome.express()] if genome else [],
)

# Evolve genome from outcome (Step 6)
if genome:
    outcome = ConvergeOutcome(
        run_id=result.run_id if hasattr(result, 'run_id') else f"code-{task_slug}",
        genome_id=genome.id,
        generation=genome.generation,
        expressed_genes=[g.name for g in genome.express()],
        invoked_genes=invoked_genes,  # Skills used during the loop
        satisfied=result.satisfied,
        score_before=result.history[0][1] if result.history else 0,
        score_after=_scorer(result.best_state) if _scorer else 0,
        depth_reached=result.depth_reached,
        elapsed_sec=result.elapsed_sec,
    )
    genome = evolve_generation(genome, outcome)
    save_genome(genome)
    save_outcome(outcome)
```

This uses `converge_tracked()` which wraps the core `converge()` kernel with automatic metrics recording AND genome field passthrough.

## Meta-Recursive Property

This command was built using itself:
- Depth 0: Draft command + skill
- Depth 1: Evaluate (is it usable? self-referential? complete?) — improve
- Depth 2: Polish and ship

The `/converge` command is an instance of `converge()`.
The explanation of `/converge` was produced by `/converge`.
The proof that it works is that it built itself.

`converge(converge_command, is_shippable, improve_skill, max_depth=2)`

## Examples

### Simple

```
/converge explain recursion
```

Depth 0: Full technical explanation (score: 55 — too complex)
Depth 1: Simplified with analogy (score: 75 — better but still wordy)
Depth 2: "A function that calls itself until it doesn't need to." (score: 92)
Converged at depth 2.

### Code

```
/converge --code fix the auth middleware to handle expired tokens
```

Depth 0: Basic try/catch (score: 60 — no refresh logic)
Depth 1: Add token refresh (score: 78 — no retry on failure)
Depth 2: Refresh + retry + exponential backoff (score: 91)
Converged at depth 2.

### Design

```
/converge --depth 5 design the notification system API
```

Depth 0: CRUD endpoints (score: 45 — missing pub/sub, preferences)
Depth 1: Add WebSocket + preferences (score: 65 — no batching)
Depth 2: Add batching + rate limiting (score: 80 — missing multi-channel)
Depth 3: Multi-channel routing (score: 92)
Converged at depth 3.

## External Proof Protocol (for cross-environment validation)

Red-team analysis (run a1248683, Feb 2026) found GRIP's compound claim is PROMISING but
not PROVEN. Current evidence: 2.5/5 points, N=4, self-scored, single environment.

### What's needed to reach PROVEN (5/5)

| Requirement | Points | Status |
|-------------|--------|--------|
| Speed trend (within session) | 1.0 | Have |
| Quality improving OR at ceiling | 0.5 | Have (self-scored, partial credit) |
| Efficiency improving | 1.0 | Have |
| N >= 6 for trend significance | 1.0 | Need (currently N=4) |
| External scoring validation | 1.0 | Need |
| N >= 20 for full proof | bonus | Need |

### Proof Protocol for External Teams

1. **Setup**: Copy `lib/converge_metrics.py` to target environment
2. **Run 6+ converge tasks** on real work (not meta-work about converge)
3. **External scoring**: Have a human or separate agent score the output (not self-scored)
4. **Control comparison**: Run 3 tasks WITH converge, 3 WITHOUT, same complexity
5. **Report**: `python3 lib/converge_metrics.py compound` — must show LIKELY or PROVEN

### Known Caveats (honest)

- Self-scoring always lands at 91 (threshold ceiling effect)
- Speed improvement may be "session warmup" not compounding
- 75% of initial runs were meta-work (building the metrics system itself)
- Elapsed times are estimates, not measured (20-40% inflation likely)
