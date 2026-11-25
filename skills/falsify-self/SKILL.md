---
name: falsify-self
description: GRIP self-falsification protocol. Catalogues performance claims, decomposes into testable implications (POPPER pattern), designs and runs falsification experiments, reports evidence grades, and feeds results into /evo genome fitness. The most important integrity feature — GRIP applying its own epistemology to itself.
status: Active
version: 1.1.0
effort: high
triggers:
  - /falsify-self
  - "falsify grip"
  - "self-falsification"
  - "audit grip claims"
  - "grip honesty check"
integrates:
  - converge.py
  - converge_metrics.py
  - evo
  - research-council
  - hypothesis-validation
  - evidence-evaluation
  - genome-monitor
see_also:
  - full-converge
  - meta-rsi-review
  - broly-meta-agent
priority: PARAMOUNT
---

# /falsify-self -- GRIP Self-Falsification Protocol

`converge(claims_inventory, all_graded, falsify_next_claim, max_depth=11)`

GRIP preaches Popperian epistemology but does not yet apply it to itself. This
skill closes that gap. Every performance claim GRIP makes is catalogued,
decomposed into testable implications, subjected to falsification experiments,
and graded with evidence. Results feed the genome so natural selection can act
on honest signals, not marketing copy.

## The Problem

GRIP makes quantitative claims in CLAUDE.md, docs, PR descriptions, and
whitepapers. These claims were never subjected to the same falsification
protocol GRIP applies to external systems via /research-council. Examples:

| Claim | Source | Status Before This Skill |
|-------|--------|--------------------------|
| "30-35% context savings" | CLAUDE.md | No published methodology |
| "440x token ratio" | efficiency-rules.md | Single data point |
| "160 skills" | CLAUDE.md | Count metric, no usage validation |
| Genome fitness trajectory | genome-shared.json | Declining, explained as non-monotonic |
| "converge() is substrate-independent" | full-converge SKILL.md | Survived one council, needs ongoing |

A system that does not falsify its own claims has no epistemic integrity.
Conventionalist stratagems (explaining away failures without new predictions)
are the specific failure mode this skill prevents.

---

## Architecture

### Convergence Structure

```
converge(CLAIM_INVENTORY, all_graded, falsify_next, max_depth=11)
  |
  +-- converge(CLAIM_1, graded, decompose_and_test, max_depth=3)
  |     |
  |     +-- Decompose: claim -> testable implications
  |     +-- Design: falsification experiments
  |     +-- Execute: run experiments, collect data
  |     +-- Grade: VERIFIED / WEAKENED / FALSIFIED / UNTESTABLE
  |
  +-- converge(CLAIM_2, graded, decompose_and_test, max_depth=3)
  |
  +-- ... (one per catalogued claim)
  |
  +-- Aggregate: compute overall honesty score
  +-- Feed: results -> genome fitness, /evo, shadow metrics
```

### POPPER Adaptation

Stanford POPPER's 5-stage pipeline adapted for self-directed falsification:

| POPPER Stage | GRIP Adaptation |
|--------------|-----------------|
| 1. Hypothesis decomposition | Claim -> testable implications (Step 3) |
| 2. Experiment design (ReAct) | Falsification experiment per implication (Step 4) |
| 3. Self-critique (relevance) | Anti-stratagem gate: is the test actually testing the claim? (Step 5) |
| 4. Sequential e-value aggregation | Running evidence score across sessions (Step 6) |
| 5. Iterative refinement | Re-test weakened claims with stronger experiments (Step 7) |

---

## Protocol

### Step 0: Load Claim Inventory

Read the live claim registry at `~/.claude/data/claim-inventory.jsonl`. If it
does not exist, bootstrap it by scanning:

```bash
# Sources to scan for claims
SOURCES=(
  "~/.claude/CLAUDE.md"
  "~/.claude/.claude/CLAUDE.md"
  "~/.claude/rules/*.md"
  "~/.claude/skills/*/SKILL.md"
  "~/.claude/docs/*.md"
  "~/.claude/articles/*.md"
  "~/.claude/site/whitepaper.md"
)
```

A claim is any statement that:
1. Contains a number (percentage, count, ratio, time)
2. Asserts a causal relationship ("X causes Y", "X prevents Y")
3. Asserts a capability ("GRIP can X", "converge() does Y")
4. Asserts a comparison ("X is better/faster/cheaper than Y")

Each claim entry:

```json
{
  "id": "claim-001",
  "text": "30-35% context budget savings (60-70k tokens/session)",
  "source": "CLAUDE.md",
  "source_line": 42,
  "category": "performance",
  "first_seen": "2026-03-23",
  "last_tested": null,
  "grade": null,
  "e_value": null,
  "shadow_metric": null,
  "implications": [],
  "experiments": [],
  "history": []
}
```

Categories: `performance`, `architecture`, `capability`, `comparison`, `process`.

---

### Step 1: Falsify the Premise First

Before falsifying conclusions, falsify the PREMISE. An investigation that
rests on an unverified premise produces rigorous-looking evidence about a
fiction — and an exogenous council asked about the conclusion will confirm
the corruption rather than catch it (2026-07-14: an `rg -rn` typo — `-r` is
`--replace`, not "recursive" — silently rewrote file reads, was mis-blamed on
the RTK tool, and drove a false two-session investigation whose council
confirmed the false premise; H798).

1. Enumerate the claims the investigation rests on ("this file contains X",
   "tool Y corrupts output", "the test fails on main").
2. For each, run the cheapest controlled ground-truth test (<30s): feed a
   known input whose correct output you can state in advance, and observe
   whether the claimed behaviour reproduces.
3. Suspect your OWN commands/flags before any external tool — `rg -r`
   (`--replace`, not recursive), `sed -i`, zsh quoting/modifiers are proven
   self-inflicted corruption sources (`rules/bash-safety.md`).

No reproduction → the premise is dead: stop, record the kill, and do not
proceed to decomposition. Only premises that survive the controlled test
earn the full protocol below.

---

### Step 2: Prioritise Claims

Not all claims warrant the same effort. Prioritise by:

| Factor | Weight | Rationale |
|--------|--------|-----------|
| Public visibility | 3x | Claims in CLAUDE.md, whitepapers, Guild pages |
| Quantitative precision | 2x | "30-35%" is more falsifiable than "significant" |
| Decision influence | 2x | Claims that drive architectural decisions |
| Last tested age | 1x | Older untested claims first |
| Staleness risk | 1x | Claims about counts/versions that change over time |

Score = sum(factor * weight). Process in descending score order.

---

### Step 3: Decompose Claim into Testable Implications

For each claim, generate 2-5 testable implications. A testable implication is
a statement that:
1. Is observable (can be measured or checked)
2. Has a clear pass/fail criterion
3. If falsified, weakens or kills the parent claim

**Example: "30-35% context budget savings"**

| # | Implication | Test | Falsification Criterion |
|---|-------------|------|------------------------|
| I1 | Sessions with GRIP use fewer tokens than baseline | Compare GRIP sessions vs non-GRIP sessions on same tasks | GRIP sessions use >= 100% of baseline tokens |
| I2 | The savings are 30-35%, not some other number | Measure actual % across N sessions | Mean savings outside [25%, 40%] with p < 0.05 |
| I3 | Savings come from efficiency protocols, not shorter sessions | Control for session length and task complexity | No correlation between efficiency protocol activation and savings |
| I4 | The savings are reproducible across users | Test with different user profiles / task types | Savings < 20% for any user category |

**Anti-stratagem gate**: For each implication, ask: "If this test passes, does
it actually provide evidence for the claim, or could it pass for unrelated
reasons?" Document the answer. If the test can pass trivially, redesign it.

---

### Step 4: Design Falsification Experiments

For each implication, design an experiment:

```json
{
  "implication_id": "I1",
  "experiment_type": "measurement|comparison|ablation|existence",
  "data_source": "session logs|git history|genome|filesystem|manual",
  "collection_method": "automatic|semi-automatic|manual",
  "sample_size_needed": 10,
  "current_sample_size": 0,
  "statistical_test": "t-test|chi-square|e-value|count|existence",
  "significance_threshold": 0.05,
  "falsification_criterion": "Mean savings < 25%",
  "confounders_identified": ["session length", "task complexity", "user skill"],
  "confounders_controlled": ["normalize by session length"]
}
```

Experiment types:

| Type | When | Example |
|------|------|---------|
| **measurement** | Claim asserts a number | Measure actual token savings |
| **comparison** | Claim asserts X > Y | Compare GRIP vs non-GRIP sessions |
| **ablation** | Claim asserts X causes Y | Disable X, observe Y |
| **existence** | Claim asserts X exists | Check filesystem / runtime |

**Automatic collection** is strongly preferred. If an experiment requires manual
data, document why and flag for future automation.

---

### Step 5: Execute Experiments

Run each experiment and collect raw data. Store results in
`~/.claude/data/falsification-results.jsonl`:

```json
{
  "claim_id": "claim-001",
  "implication_id": "I1",
  "experiment_run": "2026-03-23T14:00:00Z",
  "raw_data": { ... },
  "result": "supports|contradicts|inconclusive",
  "confidence": 0.72,
  "notes": "Sample size 8, need 10+ for significance",
  "data_path": "data/falsification/claim-001-I1-run1.json"
}
```

**Automated experiments available today:**

| Claim | Experiment | Data Source | Method |
|-------|-----------|-------------|--------|
| Skill count (160) | Count vs usage | `skills/*/SKILL.md` + session logs | `existence` + `measurement` |
| Genome fitness | Trajectory analysis | `cache/genome-history.jsonl` | `measurement` |
| Agent count (28) | Count vs invocation | `agents/*.md` + Task tool logs | `existence` + `measurement` |
| converge() abstraction | Substrate diversity | `converge-metrics.jsonl` | `measurement` |
| Token savings | Session token comparison | chat history / session metadata | `comparison` |

**Experiments requiring infrastructure build-out:**

| Claim | What's Needed |
|-------|--------------|
| 30-35% savings | Token counter per session (pre/post GRIP baseline) |
| 440x ratio | Reproduce on 5+ different retrieval tasks |
| Background agent quality | Output quality scoring rubric |

---

### Step 6: Grade and Aggregate (Sequential E-Values)

Grade each claim using sequential e-value aggregation (POPPER pattern):

**E-value**: A measure of evidence against the null hypothesis. E-values
compose multiplicatively — the product of independent e-values is itself an
e-value. This means evidence accumulates across sessions without requiring
a fixed sample size.

```
E_total = E_1 * E_2 * ... * E_n
```

Where each E_i comes from one experiment run. If E_total > 20, reject the
null (claim is supported). If E_total < 0.05, reject the claim.

**Grading rubric:**

| Grade | E-Value Range | Meaning | Action |
|-------|---------------|---------|--------|
| VERIFIED | E > 20 | Strong evidence supports claim | Keep claim, cite evidence |
| WEAKENED | 0.2 < E <= 20 | Insufficient or mixed evidence | Qualify claim, collect more data |
| FALSIFIED | E <= 0.05 | Strong evidence contradicts claim | Remove or correct claim |
| UNTESTABLE | N/A | Cannot design falsification experiment | Flag for redesign or removal |

**Conventionalist Stratagem Detection:**

After grading, check for these patterns:

1. **Ad hoc rescue**: Claim was WEAKENED, then the claim text was modified to
   accommodate the counter-evidence without generating new testable predictions.
   *Test*: diff claim text against prior version. If modified after WEAKENED
   grade, demand a new testable implication.

2. **Metric substitution**: Primary metric was WEAKENED, so a different metric
   was cited instead. *Test*: check if the metric in the claim changed across
   versions.

3. **Sample restriction**: Claim was WEAKENED on full data, then "restricted
   to appropriate contexts" without pre-registering which contexts.
   *Test*: check if context restrictions appeared after WEAKENED grade.

4. **Non-monotonic excuse**: Declining metric described as "non-monotonic" without
   a model predicting when it will recover. *Test*: if "non-monotonic" is
   invoked, demand a predicted recovery point. If recovery does not occur within
   2 generations, escalate to FALSIFIED.

---

### Step 7: Report

Generate a falsification report at `~/.claude/data/falsification-report.md`:

```markdown
# GRIP Self-Falsification Report

Generated: {timestamp}
Claims catalogued: {N}
Claims tested this run: {M}

## Results Summary

| Grade | Count | % |
|-------|-------|---|
| VERIFIED | X | X% |
| WEAKENED | Y | Y% |
| FALSIFIED | Z | Z% |
| UNTESTABLE | W | W% |

## Honesty Score

{tested_and_verified + tested_and_weakened*0.5} / {total_tested}

## Claims Detail

### FALSIFIED

[List each falsified claim with evidence summary and recommended action]

### WEAKENED

[List each weakened claim with evidence gaps and next experiments]

### VERIFIED

[List each verified claim with evidence summary]

### UNTESTABLE

[List each untestable claim with reason and recommendation]

## Stratagem Alerts

[List any detected conventionalist stratagems]

## Shadow Metrics

[Current shadow metric values vs primary metrics]
```

---

### Step 8: Feed Results

| Target | What | How |
|--------|------|-----|
| Genome | Honesty dimension added to fitness | `genome_gate.py` checks falsification grade |
| /evo | FALSIFIED claims create friction events | `evo.py` classifies as `self_contradiction` |
| /learn | Successful falsifications become insights | `learning-detector.py` captures |
| CLAUDE.md | FALSIFIED claims must be corrected or removed | Manual gate via AskUserQuestion |
| Shadow metrics | Updated each run | `data/shadow-metrics.json` |

---

## Shadow Metrics (Goodhart Protection)

Every primary metric that GRIP tracks or claims gets a shadow metric that
measures what actually matters. The shadow cannot be gamed by optimising
the primary.

| Primary Metric | Shadow Metric | Why |
|----------------|---------------|-----|
| Skill count (160) | Skill invocation count (sessions where skill was loaded and used) | A skill that exists but is never invoked is dead weight |
| Agent count (28) | Agent success rate (tasks completed / tasks attempted) | An agent that exists but fails is negative value |
| Genome fitness (avg) | Fitness trajectory slope (is it improving?) | A static fitness is stagnation, not health |
| Test count | Test failure rate on mutation (do tests catch bugs?) | Tests that always pass are theatre |
| Context savings (%) | Actual token measurements per session | Claimed savings without measurement are fiction |
| Converge satisfaction rate | Converge result quality (scored post-hoc) | Satisfying a weak criterion is trivial |
| Hook count | Hook trigger rate (how often do hooks actually fire?) | Hooks that never fire are unused infrastructure |
| PR review score | Post-merge defect rate (do reviewed PRs have fewer bugs?) | High scores with high defects means bad scoring |

Shadow metrics are stored at `~/.claude/data/shadow-metrics.json`:

```json
{
  "last_updated": "2026-03-23T14:00:00Z",
  "metrics": {
    "skill_invocation": {
      "primary": "skill_count",
      "primary_value": 160,
      "shadow_value": null,
      "collection_method": "session log analysis",
      "last_measured": null,
      "health": "unmeasured"
    }
  }
}
```

Health states: `healthy` (shadow tracks primary), `divergent` (shadow contradicts
primary), `unmeasured` (no data yet), `stale` (data older than 30 days).

---

## Live Context (DCI)

**GRIP version:** !`cd ~/.claude && git log --oneline -1 2>/dev/null`
**Claims catalogued:** !`wc -l < ~/.claude/data/claim-inventory.jsonl 2>/dev/null || echo "0 (not yet bootstrapped)"`
**Last falsification run:** !`tail -1 ~/.claude/data/falsification-results.jsonl 2>/dev/null | python3 -c "import sys,json; print(json.loads(sys.stdin.read()).get('experiment_run','never'))" 2>/dev/null || echo "never"`
**Genome avg fitness:** !`python3 -c "import json; g=json.loads(open('$HOME/.claude/cache/genome.json').read()); print(f'{g.get(\"avg_fitness\", 0):.3f}')" 2>/dev/null || echo "unknown"`
**Shadow metrics health:** !`python3 -c "import json; m=json.loads(open('$HOME/.claude/data/shadow-metrics.json').read()); h={v['health'] for v in m['metrics'].values()}; print(', '.join(sorted(h)))" 2>/dev/null || echo "not initialised"`

---

## Worked Example: Falsifying "30-35% Context Savings"

### Claim Text

> "Efficiency Protocols: 30-35% context budget savings (60-70k tokens/session)"
> Source: `~/.claude/.claude/CLAUDE.md`, line ~8

### Step 3: Decompose

| # | Implication | Observable |
|---|-------------|-----------|
| I1 | GRIP sessions consume measurably fewer tokens than equivalent non-GRIP sessions | Token counts from session metadata |
| I2 | The reduction is specifically 30-35%, not 10% or 50% | Distribution of savings across sessions |
| I3 | The savings come from GRIP efficiency protocols (caching, batching, dependency guardian), not from shorter sessions or simpler tasks | Correlation between protocol activations and savings |
| I4 | The savings persist across different task types (code, research, planning) | Stratified analysis by task type |

### Step 4: Design Experiments

**E1: Paired Task Comparison**
- Take 5 representative tasks (feature implementation, bug fix, research, planning, refactoring)
- For each: estimate token usage with and without GRIP protocols
- Compare: is there a consistent 30-35% reduction?
- Data source: session history analysis (chat logs, token counts)
- Confounder control: normalise by task complexity (LOC changed, files touched)

**E2: Protocol Ablation**
- In N sessions, track which efficiency protocols fired
- Measure: sessions where dependency-guardian blocked a read (50k saved per block)
- Measure: sessions where file-read-optimizer prevented re-reads
- Measure: sessions where GRIP-First prevented agent spawning
- Sum the measured savings per session
- Compare to the 30-35% claim

**E3: Historical Baseline**
- Examine pre-GRIP Claude Code sessions (if available)
- Compare average session token usage before and after GRIP installation
- This is the strongest test but requires baseline data

### Step 5: Execute (What We Can Do Today)

```bash
# Count dependency guardian blocks (each saves ~50k tokens)
rg "dependency.guardian.*block" ~/.claude/logs/ 2>/dev/null | wc -l

# Count GRIP-First retrieval successes (each saves ~88k vs Explore agent)
rg "GRIP.*First.*resolved" ~/.claude/logs/ 2>/dev/null | wc -l

# Count file-read-optimizer cache hits
rg "cache.hit.*skip" ~/.claude/logs/ 2>/dev/null | wc -l
```

### Step 6: Grade

Suppose we find:
- Dependency guardian: 3 blocks in 20 sessions = 150k tokens saved = ~7.5k/session
- GRIP-First: 5 resolutions in 20 sessions = ~440k saved = ~22k/session
- File-read cache: unknown (no logging yet)
- Total measurable savings: ~29.5k/session
- Average session budget: 200k tokens (1M context, ~20% typical usage)
- Measured savings: 29.5k / 200k = ~14.75%

**Result**: Measured savings of ~15% vs claimed 30-35%. E-value: 0.8 (weak
evidence against claim). **Grade: WEAKENED**.

**Recommended action**: Either (a) add instrumentation to measure all savings
sources (batching, concise communication, context refresh), or (b) revise
claim downward to "15-20% measurable savings with potential for 30-35% including
unmeasured channels", or (c) build the measurement infrastructure and re-test.

**Stratagem check**: If the response is "the unmeasured channels account for
the remaining 15-20%", that is an ad hoc rescue unless accompanied by a
testable prediction: "After adding instrumentation for X, Y, Z, we predict
measured savings will be 28-38%."

---

## Integration Points

| Component | Integration | Direction |
|-----------|-------------|-----------|
| `/evo` | FALSIFIED claims become `self_contradiction` friction events | falsify-self -> evo |
| `/evo` | Genome fitness gains `honesty` dimension | falsify-self -> genome |
| `/learn` | Successful falsifications become permanent insights | falsify-self -> learn |
| `/research-council` | Shares falsification infrastructure (POPPER stages 1-5) | bidirectional |
| `genome-monitor` | Alerts when shadow metrics diverge from primaries | genome -> falsify-self |
| `CLAUDE.md` | FALSIFIED claims trigger mandatory correction | falsify-self -> CLAUDE.md |
| `converge_metrics.py` | Uses convergence metric recording for experiment data | falsify-self -> converge |
| `hypothesis-validation` | Reuses H0/H1 framework for each implication | falsify-self -> hypothesis |
| `evidence-evaluation` | CRAAP scoring for external evidence quality | falsify-self -> evidence |

---

## Commands

```bash
/falsify-self                     # Full run: catalogue, decompose, test, report
/falsify-self bootstrap           # Scan sources, create initial claim inventory
/falsify-self claim <id>          # Test a specific claim
/falsify-self report              # Generate report from existing results
/falsify-self shadow              # Show shadow metrics vs primaries
/falsify-self stratagems          # Check for conventionalist stratagems
/falsify-self add "<claim text>"  # Manually add a claim to inventory
```

---

## Execution (Broly Integration)

For full runs, use Broly CLUSTER level (3-5 agents):

```
/broly cluster /falsify-self

  Agent 1 (Cataloguer): Scan sources, build/update claim inventory
  Agent 2 (Experimenter): Design and run experiments for top-priority claims
  Agent 3 (Grader): Apply e-value aggregation, detect stratagems
  Coordinator: Merge results, generate report, feed genome
```

For single-claim testing, THREAD level is sufficient:

```
/broly thread /falsify-self claim claim-001
```

---

## Scheduling

| Trigger | Scope | Effort |
|---------|-------|--------|
| After any CLAUDE.md edit | Re-scan for new/changed claims | Low |
| After /evo completes | Check if genome fitness narrative matches data | Low |
| Weekly (manual or launchd) | Full falsification run | High |
| Before any whitepaper/article | Verify all cited claims | Medium |
| After RSI sprint | Verify sprint claims (PRs merged, tests added) | Medium |

---

## Anti-Patterns

| Anti-Pattern | Why Bad | Prevention |
|--------------|---------|------------|
| Testing only easy claims | Selection bias toward VERIFIED | Priority scoring includes difficulty |
| Lowering falsification threshold after failure | Conventionalist stratagem | Thresholds set at experiment design, immutable |
| Claiming UNTESTABLE to avoid testing | Intellectual laziness | UNTESTABLE requires documented reason AND redesign proposal |
| Testing the same claim repeatedly without new data | Wasted context | E-values compound; only re-test with new evidence |
| Gaming shadow metrics | Goodhart on Goodhart | Shadow metrics are read-only; cannot be targets |
| Reporting only VERIFIED claims | Confirmation bias | Report MUST include all grades |

---

## File Locations

| File | Purpose |
|------|---------|
| `data/claim-inventory.jsonl` | All catalogued claims |
| `data/falsification-results.jsonl` | Raw experiment results |
| `data/falsification-report.md` | Human-readable report |
| `data/shadow-metrics.json` | Shadow metric current values |
| `data/e-value-accumulator.json` | Running e-values per claim |
| `data/stratagem-alerts.jsonl` | Detected conventionalist stratagems |

---

## Bootstrap Checklist

When this skill is first activated:

1. [ ] Create `data/claim-inventory.jsonl` by scanning all sources
2. [ ] Create `data/shadow-metrics.json` with initial unmeasured entries
3. [ ] Create `data/e-value-accumulator.json` with neutral priors
4. [ ] Run first falsification on the highest-priority claim
5. [ ] Add `honesty` dimension to genome fitness function
6. [ ] Wire `genome-monitor` to alert on shadow metric divergence
7. [ ] Add post-CLAUDE.md-edit hook to trigger claim re-scan

---

## The Meta-Constraint

This skill is itself a claim: "GRIP can meaningfully falsify its own claims."

Testable implication: After 3 full runs, at least one claim will have been
WEAKENED or FALSIFIED. If all claims are VERIFIED after 3 runs, this skill
is either working correctly on an honest system, or it is insufficiently
rigorous. The tie-breaker: check whether any claim text was modified between
runs to accommodate weak evidence (stratagem detection).

If after 5 full runs, zero claims are WEAKENED or FALSIFIED, and no stratagems
are detected, the most likely explanation is that the experiments are too weak.
Escalate: invoke /research-council to design stronger experiments from an
adversarial perspective.

---

## Token Budget

| Component | Tokens |
|-----------|--------|
| Skill load | ~3000 |
| Bootstrap (first run) | ~15000 |
| Per-claim testing | ~2000-5000 |
| Full run (10 claims) | ~30000-50000 |
| Report generation | ~2000 |
| Broly cluster overhead | ~10000 |
| **Typical full session** | **~50000-70000** |

---

## Changelog

| Version | Date | Changes |
|---------|------|---------|
| 1.0.0 | 2026-03-23 | Initial design: POPPER adaptation, shadow metrics, stratagem detection, genome integration |
| 1.1.0 | 2026-07-15 | Step 1 "Falsify the Premise First" inserted (2026-07-14 rg -rn/RTK false-investigation incident, H798); prior Steps 1-7 renumbered 2-8 |
