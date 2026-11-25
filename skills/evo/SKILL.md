---
name: evo
description: Fractal facet evolution engine. Re-analyses Claude Code's /insights facet files with GRIP context to calibrate friction metrics, detect false flags, and feed calibrated insights into the learning pipeline, genome, and CUBE.
status: Active
effort: medium
version: 1.1.0
triggers:
  - /evo
  - "evolve facets"
  - "calibrate insights"
  - "false flag analysis"
see_also:
  - insights-pipeline
  - learn
  - converge
  - self-improvement-engine
  - broly-meta-agent
---

> **PARAMOUNT — DISPATCH PROTOCOL**: Always invoke via `Agent(run_in_background=True)`.
> NEVER run inline via Bash. See `rules/evo-dispatch-protocol.md`.

# /evo -- Fractal Facet Evolution

`converge(corpus, calibrated_understanding, evo_improve, max_depth=7)`

Re-analyses Claude Code's `/insights` facet files with GRIP context to produce
calibrated friction metrics. False-flag rates EMERGE from evidence density,
never hardcoded.

## The Problem

Claude Code's `/insights` uses an LLM to classify session friction. It has no
knowledge of GRIP infrastructure (149 skills, hooks, modes, genome). This creates
systematic overcounting:

| Friction Type | CC Raw | Typical FF Rate | Why |
|---------------|--------|-----------------|-----|
| wrong_approach | 51.5% | ~50% | GRIP steering = normal workflow, not failure |
| misunderstood_request | 16.2% | ~25% | Context-adding = orchestration, not confusion |
| buggy_code | 21.7% | ~30% | Hook blocks + gate denials = not bugs |

## Fractal Architecture

"Fractal" names the shape of the question, not five nested loops. The same
question (is this friction real?) is asked at each tier, from the whole corpus
down to one detail. In `lib/evo.py` only `evo()` calls `converge()`, once. Its
improve step, `evo_improve`, runs Tiers 1 to 3 as ordinary function calls, and
a second round of the same loop runs Tier 4:

```
Tier 0: Corpus    converge(all_facets, advocate has run, evo_improve)
Tier 1: Facet     analyse_facet(one_session)           -> verdict
Tier 2: Friction  classify_friction(one_event)         -> true or false flag
Tier 3: Evidence  match_evidence(detail, markers)      -> evidence found
Tier 4: Advocate  devils_advocate(state)               -> weak claims discounted
```

The loop stops on the first state the advocate has seen (`EvoState.challenged`),
so every run gets Tier 4 exactly once. Until GRIP#9533 it stopped at a
confidence of 0.8, which the first round reaches from 400 facets, so larger
corpora skipped Tier 4 and smaller ones ran it again every round. Confidence is
now reported, not a stop rule: the first round sets it from the facet count and
the advocate adds 0.1.

Discount factors emerge from evidence density (keyword matching against
known hooks, steering patterns, environment errors). No magic numbers.

## Usage

```bash
/evo                    # Full analysis (one converge() loop, at most 7 rounds)
/evo trajectory         # Show convergence trajectory over time
/evo json               # Machine-readable output
```

## Execution

Always via isolated Agent (see Dispatch Protocol below and `rules/evo-dispatch-protocol.md`):

```python
Agent(
    description="Run /evo fractal evolution",
    subagent_type="general-purpose",
    run_in_background=True,
    prompt="Run: python3 ~/.claude/lib/evo.py run 7\nReturn a 2-line summary: facet count + calibrated FD."
)
```

Direct invocations (for local debugging only — NEVER in autonomous loops):
```bash
python3 ~/.claude/lib/evo.py run [depth]       # output is 80+ lines — use Agent in loops
python3 ~/.claude/lib/evo.py json [depth]      # JSON output
python3 ~/.claude/lib/evo.py trajectory        # Convergence history
```

## Dispatch Protocol (PARAMOUNT)

`/evo` wraps `lib/evo.py run`, a long Python subprocess that routinely outlives
agent watchdog timeouts. **Never invoke `/evo` inline or inside a chain coordinator.**

**Wrong** — causes polling-loop / watchdog expiry (falsified three times: sessions
flagged in `/evo polling-loop (3rd recurrence)` in session context):
```
Agent("/insights → /learn → /evo", run_in_background=true)
```

**Correct** — two separate dispatches (on 2026-04-27, W11-bg, one coordinator chaining
every step ended before `/evo` finished, leaving `/learn` unrun):
```
# Dispatch 1: fast chain (fire, wait for return)
Agent("/insights → /learn", run_in_background=true)

# Dispatch 2: long evo unit (fire-and-forget — data lands in insights.jsonl regardless)
Agent("/evo", run_in_background=true)
```

`/evo`'s output writes to `learning/insights.jsonl` directly, so Dispatch 2 succeeding
is not required for the pipeline to complete. The split is safe: Dispatch 1 reads
whatever `/evo` last wrote; both dispatches run concurrently.

## Breathing Pause — RTK Dividend

At each breathing pause between Fibonacci waves, record the RTK wave delta:

```bash
python3 $HOME/.claude/lib/rtk_sprint_dividend.py wave N
```

This runs in ~100ms and outputs one compact line showing compression gain for
that wave. The data feeds the Sprint Dividend report at session close and
provides per-wave evidence that RTK is actively compressing RSI Bash output.

## What It Feeds

| System | What | How |
|--------|------|-----|
| Learning Pipeline | Calibrated insights | `learning/insights.jsonl` |
| Convergence Tracker | FD trajectory | `learning/evo-convergence.jsonl` |
| CUBE | Principle scores | `cache/cube-state.json` |
| Genome | Micro-skill proposals | Via `apply_genesis_proposals()` |
| KONO | Episodic summaries | Via embeddings pipeline |
| RTK Dividend | Wave compression delta | `state/rtk/sprint_*.json` |

## Evidence Classification

Three evidence sets, weight emerges from match density:

- **GATE_EVIDENCE**: hook, gate, block, permission, denied, guardrail, pretooluse
- **STEERING_EVIDENCE**: user corrected, user pointed out, user interrupted, user redirected
- **ENVIRONMENT_EVIDENCE**: api error, timeout, lock file, context window, rate limit

Match density formula: `weight = min(0.4, matches * 0.12)` per evidence set.
Known hooks get +0.25 bonus. Total FF rate capped at 0.95.

## Devil's Advocate (Tier 4)

High false-flag rates (>30%) with weak evidence (<2 matches) get challenged:
FF rate reduced by 40%. This prevents the system from over-discounting
friction that IS real.

## Output

```
================================================================
  /evo -- Fractal Facet Evolution Report
================================================================

  Facets:     359
  Raw FD:     1.362
  Cal FD:     1.199
  FF rate:    12.0%
  Confidence: 0.76

  Type                          Raw    True    FF%
  wrong_approach                156   137.9   11.6%
  buggy_code                     58    52.2   10.0%
  misunderstood_request          53    46.6   12.1%

  Verdicts:
    grip_steering          131 (36.5%)
    true_friction          106 (29.5%)
    clean                   97 (27.0%)
    mixed                   24 (6.7%)
    intentional_gate         1 (0.3%)

================================================================
```

## Yolo Usage Tile

Below the main /evo report, a secondary tile summarises recent autonomous-loop
activity from `~/.claude/logs/yolo-mode.log`:

```
Yolo Usage (7d)
  Activations: 4
  Bypass fires: 23 (5.8/activation)
  Avg TTL granted: 150.0 min
  Per active hour: 2.3
  By activator:
    /ultrado: 3
    /rsi-optimal: 1
  By gate:
    pre-check-dispatch: 12
    code-write: 5
    plan-review: 3
```

Parsing lives in `lib/yolo_metrics.py` (dependency-free). An `ALERT:` line
appears when bypass-per-active-hour crosses 50/h, which is a signal that
either the gate set is too permissive or an autonomous loop is stuck in a
hot-fire pattern worth investigating. Tests: `tests/test_yolo_metrics.py`
against the pinned fixture at `tests/fixtures/yolo_log_sample.log`.

## Integration with /learn

`/evo` is Step 1.7 in the `/learn` RSI loop:

```
Step 1:   Pattern Detection         (optim.sh detect)
Step 1.4: Consolidate Insights      (learning_detector.py consolidate)
Step 1.5: Ingest Agent Insights     (learning_detector.py ingest)
Step 1.6: Generate Rules            (learning_detector.py generate-rules)
Step 1.7: Facet Evolution           (Agent /evo, run_in_background=true)
Step 2:   Autonomous Learning       (learning_detector.py scan)
```

## Integration with /insights

After running CC's `/insights`, invoke `/evo` to calibrate:

```bash
# 1. Run /insights (generates facets + report.html)
# 2. Version the report
python3 lib/insights_learner.py run
# 3. Calibrate with GRIP context — dispatch as isolated background Agent, NEVER inline
#    Agent("/evo", run_in_background=true)   <-- correct
#    python3 lib/evo.py run                  <-- WRONG: outlives watchdog, causes polling-loop
```

## Convergence Trajectory

Each `/evo` run records to `learning/evo-convergence.jsonl`:

```json
{
  "timestamp": "2026-03-11T...",
  "raw": 1.362,
  "calibrated": 1.199,
  "ff_rate": 0.12,
  "confidence": 0.76,
  "facets": 359,
  "types": {"wrong_approach": {"raw": 156, "true": 137.9, "ff": 0.116}},
  "proposals": 1,
  "cube": {"SOLID": 0.662, "KISS": 0.617, "DRY": 0.762}
}
```

Over time: `python3 lib/evo.py trajectory` shows whether calibrated FD
is contracting (genuine improvement) or flat (Goodhart ceiling).

## Structural Mapping (Gentner SMT)

| converge() | /evo |
|------------|------|
| state | EvoState (polymorphic across tiers) |
| criterion | the advocate has run (`EvoState.challenged`); confidence is reported, not a stop rule |
| improve | evo_improve (dispatches by tier: round 1 runs Tiers 1 to 3 over the corpus, round 2 runs Tier 4) |
| fixed_point | true friction density (asymptotic limit) |
| depth | at most 7 rounds of the one loop; the tiers (corpus -> facet -> event -> evidence -> advocate) are its call path, not its depth |

G1-G3 structural mapping validated. Quantitative Banach theorems do NOT
transfer (improve is multi-stage pipeline, not contraction map).

## Known Limitations

- `friction_detail` text is often too brief for strong evidence matching
- Current FF rate (~12%) is lower than manual analysis (~40%) due to
  limited context (facet summary vs full session JSONL)
- Enhancement needed: read session JSONL for richer evidence sourcing
- Self-scored confidence, not externally validated

## Changelog

| Version | Gen | Changes |
|---------|-----|---------|
| 1.0.0 | 475 | Initial: fractal converge, evidence-based FF, devil's advocate |
| 1.1.0 | 2026-11-25 | The advocate runs once on every analysis (GRIP#9533), so reported confidence changes. Measured on the same synthetic corpora: before, 50 and 250 facets reported 0.85 and 400 reported 0.80, because the advocate re-ran on small corpora until 0.8 and was skipped on large ones; now 50 facets report 0.55 and 400 report 0.90, rising with the corpus. On real data the advocate's discount never fires (one evidence item lifts a false-flag rate by at most 0.25, below its 0.3 threshold), so its only effect is the +0.1 |
