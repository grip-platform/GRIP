---
name: ultrado
description: >
  Ultrado Magnifico della Convergenza Infinita. The recursive falsification
  engine at the heart of GRIP. Converge is the kernel; ultrado is the runtime.
  Every loop exits on falsification — confirmed or falsified, both are science.
  Adaptive precision: FAST for execution, CAREFUL for surgery, VERIFY for claims.
  Crash-resilient checkpoints between Fibonacci waves. Broly council with devil's
  advocate for sensitive work. Part Italian Brainrot, part recursive self-improvement,
  all action. Fame: MYTHIC.
status: Active
effort: max
priority: paramount
version: 2.0.0
created: 2026-04-06
updated: 2026-04-06
triggers:
  - /ultrado
  - "do everything"
  - "full autonomous"
  - "be all and end all"
  - "ultrado magnifico"
integrates:
  - ultrado_checkpoint.py
  - ultrado_precision.py
  - converge.py
  - converge_combinators.py
  - converge_dna.py
  - converge_metrics.py
  - hypothesis_engine.py
  - rsi-optimal
  - ultraplan
  - ultrathink
  - converge
  - full-converge
  - falsify-self
  - broly-meta-agent
  - auto-ship
  - hypothesis-driven-development
  - saving-state
  - context-refresh
  - scientific-measurement
see_also:
  - autonomous-rsi-sprint
  - sprint-loop
---

# /ultrado -- Ultrado Magnifico della Convergenza Infinita

*The MYTHIC-tier command. Recursive falsification engine.*

```
TIER: MYTHIC                    FAME: 9999
SPECIES: Command/RecursiveFalsificationEngine
ORIGIN: Cape Town + Italian Brainrot Universe
ABILITIES: Recursive Convergence | Adaptive Precision | Falsification Criterion |
           Crash Resilience | Broly Devil's Advocate | Fibonacci Breathing |
           Auto-Ship Bombardment | Never-Stop Protocol
CATCHPHRASE: "ULTRADO ULTRADO ULTRADO!"
KERNEL: converge(state, hypothesis_resolved, adaptive_improve, depth=fibonacci)
```

One command. Everything happens. Nothing stops. Everything is falsifiable.

**Plain English summary**: an autonomous loop that breaks the goal into
work sprints with rest breaks (Fibonacci waves), commits to a falsifiable
prediction for each sprint, ships the result, then either proves or
disproves the prediction before moving on. Stops on completion or when
the context window fills up. Never stops on a halting gate during
yolo mode — an auto-pilot mode that relaxes approval gates so the loop
runs end-to-end while keeping quality checks and force-push protection
on (4-hour ceiling).

## The Core Identity

```
ultrado = converge(goal, falsified_or_confirmed, improve=ultrado, depth=fibonacci)
```

Converge is the kernel. Ultrado is the runtime. The difference: ultrado adds
Fibonacci wave breathing, crash-resilient checkpoints, adaptive precision routing,
and the InfiniteMachine pattern that makes it sustainable over hours.

`/converge` is now syntactic sugar for single-wave ultrado.
`/full-converge` is syntactic sugar for recursive ultrado.

## Usage

```bash
/ultrado "build the Guild knowledge dashboard"
/ultrado 8h "refactor memory provenance architecture"
/ultrado --depth 11 "formal convergence proofs"
/ultrado --careful "migrate the auth database"
/ultrado --fast "fix the CSS padding"
/ultrado                    # resumes from checkpoint or remaining work
```

## What It Does

```
/ultrado = /recall + /ultraplan + converge(recursive, falsification) + /auto-ship + /learn + /save + InfiniteMachine
```

## Architecture

```
ultrado v2.0
|-- ABSORB (checkpoint -> session-context -> ask user)
|-- PLAN (ultraplan + hypothesis pre-registration)
|
|-- WAVE LOOP (Fibonacci: 1,1,2,3,5,8)
|   |-- detect_precision(wave.tasks) -> FAST|CAREFUL|VERIFY
|   |
|   |-- converge(wave)  <-- THE KERNEL
|   |   |-- criterion = hypothesis_resolved(wave.claim)
|   |   |-- improve = adaptive_improve(precision)
|   |   |   |-- FAST:    edit -> test -> done
|   |   |   |-- CAREFUL: ultraplan -> ultrathink -> edit -> verify
|   |   |   +-- VERIFY:  broly(council + devil's advocate) -> falsify
|   |   |
|   |   |-- TASK LOOP (recursive converge, depth <= 3)
|   |   |   +-- ATOM LOOP (leaf converge, depth <= 2)
|   |   |
|   |   +-- max_depth = fibonacci(wave_num)
|   |
|   |-- SHIP (commit -> push -> PR -> merge)
|   |-- MEASURE (hypothesis verification + anti-drift)
|   |-- LEARN (genome evolution + insights)
|   +-- CHECKPOINT (crash-resilient state save)
|
|-- PERSIST (/save)
+-- BREATHE (context gate -> compact or fresh)
```

## The Loop

### Phase 0.0: PRE-FLIGHT — WORKTREE ISOLATION (PARAMOUNT, MANDATORY)

**This is the first tool call of any /ultrado invocation. Nothing else happens
until the pre-flight clears.** Rule 13 (`rules/efficiency-rules.md`) escalates
Gate O to PARAMOUNT when three conditions coincide — concurrent sessions,
autonomous loop, shared main tree — but Gate O's ambient detection has a
cold-start gap: `is_ultrado_context()` relies on env vars or checkpoint files,
neither of which exist in the first minutes of a fresh skill invocation. The
skill MUST close that gap explicitly.

Run both of these as a single Bash call, before any Read, Edit, or Grep:

```bash
mkdir -p ~/.claude/state/ultrado
python3 -c "
import json, os, time
ts = time.strftime('%Y-%m-%dT%H:%M:%S%z') or time.strftime('%Y-%m-%dT%H:%M:%SZ')
marker = os.path.expanduser(f'~/.claude/state/ultrado/session-{os.getpid()}.json')
open(marker, 'w').write(json.dumps({
    'timestamp': ts, 'context': 'ultrado',
    'pid': os.getpid(), 'cwd': os.getcwd(),
}))
print(f'ultrado marker written: {marker}')
" && PYTHONPATH=$HOME/.claude $HOME/.claude/venv/bin/python -c "
from lib.gates.session_isolation import require_worktree_isolation
msg = require_worktree_isolation('ultrado')
if msg:
    print(msg)
    import sys; sys.exit(2)
print('[ultrado pre-flight] worktree isolation OK — proceeding')
"
```

Dispatch on the exit code:

| Exit | Meaning | What to do |
|------|---------|------------|
| 0 | Pre-flight clear | Continue with Phase 0 step 1 |
| 2 | PARAMOUNT block | HALT. Present the operator with **one AskUserQuestion** offering three options (see below). |
| other | Python/env error | Report the error to the operator and ask whether to proceed without the pre-flight (operator judgment). |

**Exit-2 operator choice** — present exactly once, no re-prompting:

> Gate O blocked /ultrado: concurrent sessions + shared main tree detected.
> Choose:
> 1. **Worktree (recommended)** — `bash ~/.claude/lib/grip-worktree.sh add /tmp/<name> feat/<branch>`, then re-invoke /ultrado inside. No context loss, cleanest isolation.
> 2. **Bypass-marker** — write a TTL'd marker so hook subprocesses relax Gate O for this session without a relaunch (no context loss). Run:
>    ```bash
>    PYTHONPATH=$HOME/.claude $HOME/.claude/venv/bin/python -c "
>    from lib.gates.session_isolation import write_bypass_marker
>    write_bypass_marker('V>>', 'unblock /ultrado', ttl_seconds=14400)
>    print('isolation bypass marker written (4h TTL)')
>    "
>    ```
>    Then continue from Phase 0.1. Force-push protection is NOT relaxed by this marker.
> 3. **Abort** — stop here, no changes.

**Why both the marker AND the explicit call**: the marker populates the
ambient detector (`is_ultrado_context()`) so OTHER sessions' Gate O checks
see this session as active via `state/ultrado/session-<pid>.json`. The
explicit `require_worktree_isolation` call bypasses ambient detection
entirely and uses the authoritative `context="ultrado"` argument — this is
what actually blocks THIS session if the preconditions hold, because the
ambient detector can't see its own freshly-started run.

**Escape hatches** (operator choice, audit-logged):
- One-session override: `GRIP_ISOLATION_BYPASS=1 /ultrado ...` — must be
  set before `claude` starts (requires relaunch); writes to `logs/isolation-bypass.log`
- Bypass-marker (no relaunch): `write_bypass_marker('V>>', reason, ttl_seconds)`
  from `lib.gates.session_isolation` — symmetric with yolo mode activation
- Env signal (alternative to checkpoint): `export GRIP_ULTRADO_ACTIVE=1`
  before invoking — makes ambient detection return True without a checkpoint file

**Origin**: Gen 481. The H021 ultrado was run in the shared main tree
with 3 concurrent claude sessions, and Gate O failed to block it because
`is_ultrado_context()` returned False (no env var, no checkpoint yet).
Only a `.git/index.lock` collision mid-commit surfaced the race — the
commit survived by luck, not by enforcement. This pre-flight closes the
detection gap at the skill layer so the protection Rule 13 promises is
real for every future ultrado run.

### Phase 0.1: ACTIVATE YOLO MODE (MANDATORY, after pre-flight)

After Phase 0.0 pre-flight clears, activate yolo mode so the loop runs
to completion without stopping for halting gates (approach-verification,
confidence-gate, pre-check-dispatch, plan-review, code-write,
stash-safety, push-to-main). Quality and JIT optimisation
gates (DRY/KISS/BIG-O/CC, knowledge-work-scorer, correct-by-construction)
keep firing — /ultrado RELIES on those to converge to optimal code.

Secrets detection (Gate 3) was listed above until 2026-08-18 and is
deliberately absent now. Its rationale — "git-crypt encrypts these anyway"
— was measured false: git-crypt matches PATHS while the gate matches
FILENAMES, and `git check-attr filter` over all 22 patterns at realistic
staging paths returned 0 encrypted. A /ultrado loop that tries to stage a
dotenv or a private key is denied, exactly as an interactive session is.

Force push is NEVER bypassed by yolo mode. Rewriting remote history still
requires `GRIP_AUTONOMOUS=<key>:2` with stored secret + consent phrase.

```bash
PYTHONPATH=$HOME/.claude $HOME/.claude/venv/bin/python -c "
from lib.gates.yolo_mode import activate_yolo
path = activate_yolo('/ultrado', ttl_seconds=14400, reason='${GOAL:-ultrado session}')
print(f'yolo mode activated: {path} (4h TTL)')
"
```

**Cleanup is mandatory** — at the end of the loop (Phase 7 PERSIST) or
on explicit abort, deactivate the marker:

```bash
PYTHONPATH=$HOME/.claude $HOME/.claude/venv/bin/python -c "
from lib.gates.yolo_mode import deactivate_yolo
deactivate_yolo('/ultrado')
print('yolo mode deactivated')
"
```

If the skill crashes without deactivating, the TTL (default 4 hours)
auto-invalidates the marker at next gate check. The session-start hook
sweeps expired markers on the next claude launch.

See `docs/SAFETY-GATES.md` section "Yolo Mode" and
`plans/yolo-mode-unified-autonomous.md` for the full gate matrix and
safety invariants.

### Phase 0: ABSORB (context assembly)

After pre-flight clears, build the full mental model:

1. **Check for crash checkpoint** (`lib/ultrado_checkpoint.py resume`)
   - If checkpoint exists: report wave reached, ask to resume or restart
   - If resuming: skip to the next incomplete wave
2. Load session context (`rules/session-context.md`)
3. Check for remaining work from prior sessions
4. If no goal provided, continue remaining work (smart resume)
5. GRIP-First retrieval: session -> KONO -> known paths -> Grep
6. Read the plan if one exists (`plans/*.md`)

**Time budget**: < 30 seconds. No Explore agents. Tier 0-2 only.

If the user provided a goal, that overrides remaining work.
If no goal AND no remaining work AND no checkpoint, ask the user.

### Phase 0.5: ADAPT WAVE PLAN TO CONTEXT BUDGET (MANDATORY)

Before planning waves, compute how many are actually affordable given the
current context usage. A fixed 6-wave Fibonacci plan at 72% usage crashes
into the 85% gate mid-wave and forces an emergency compact. This phase
closes that gap by adapting the wave count to the remaining runway.

Call `lib/ultrado_precision.py::adapt_wave_plan` and feed the result into
Phase 1:

```python
from lib.ultrado_precision import adapt_wave_plan, WAVE_COST_P50, WAVE_COST_P90

# Pick cost: p90 if the goal mentions VERIFY/CAREFUL territory, else p50.
cost = WAVE_COST_P90 if goal_is_careful_or_verify(goal) else WAVE_COST_P50

plan = adapt_wave_plan(
    current_context_pct=<reported usage>,
    total_budget=<200_000 or 1_000_000>,
    wave_cost=cost,
)

print(f"[ultrado] {plan.reason}")

if plan.handoff:
    # Not enough runway — pivot straight to /save + fresh session.
    save_state_and_exit()
```

Tier matrix (rounded, p50, 200k budget):

| Context % | Runway | Tier | Waves | Operator output |
|-----------|--------|------|-------|-----------------|
| 0-30% | 55-85% | full | 6 | "FULL Fibonacci plan" |
| 30-60% | 25-55% | truncated | 2-5 | "TRUNCATED plan: N waves" |
| 60-70% | 15-25% | single | 1 | "SINGLE WAVE mode, then handoff" |
| 70%+ | <15% | handoff | 0 | "HANDOFF mode: /save + fresh session" |

p90 costs (CAREFUL/VERIFY-heavy) shift each band up by ~10%.

**Yolo TTL coupling**: if `waves > 0`, set the yolo marker TTL to
`min(4h, projected_wave_duration * waves)` so autonomous bypasses don't
outlive the planned work.

**Why**: V>> flagged the failure mode on 2026-04-17 ultrado at 16%
context: "how long should you go for autonomously? take into account the
checkpoints." GRIP#2295 + hypothesis H090 track this. Without this phase,
context-gate fires mid-wave cost ~4-6k tokens of emergency compaction.

**Time budget**: < 5 seconds. Pure function, no I/O.

### Phase 1: PLAN (falsifiable design)

Run ultraplan inline (not remote -- we have the context):

1. Gap analysis: current state -> target state
2. Fibonacci wave decomposition, capped at `plan.waves` from Phase 0.5
   (default series 1, 1, 2, 3, 5, 8 — take the first N where N is the
   context-adapted count)
3. **Hypothesis pre-registration (MANDATORY)**:
   - Each wave gets a falsifiable claim
   - Each claim has: metric, prediction, deadline
   - Register via `lib/hypothesis_engine.py`
4. **Precision classification** of each wave's tasks:
   - Run `detect_precision()` from `lib/ultrado_precision.py`
   - Report: "W1: FAST, W2: FAST, W3: CAREFUL, W4: VERIFY"
5. Write plan to `plans/{descriptive-name}.md`

**Time budget**: < 2 minutes. One plan file. No paralysis.

### Phase 2: EXECUTE (Recursive Fibonacci Wave Convergence)

For each wave in the Fibonacci sequence:

```python
for wave_num in range(1, waves + 1):
    # Adaptive precision for this wave
    precision = detect_precision(wave.description, override=user_override)
    
    # THE KERNEL: recursive convergence with falsification criterion
    converge(
        state=wave.tasks,
        criterion=lambda s: hypothesis_resolved(wave.hypothesis, s),
        improve=adaptive_improve(precision),
        max_depth=fibonacci(wave_num),
    )
```

#### Adaptive Precision Routing

The `improve()` function dispatches based on precision level:

**FAST** (default -- execution bias preserved):
```
implement directly (edit/write) -> run tests -> verify output
```
No planning overhead. This is the mode for 80% of work: docs, tests,
UI tweaks, config changes, simple feature code.

**CAREFUL** (sensitive surgery):
```
ultraplan(task) -> ultrathink(approach) -> implement -> verify -> review
```
Used for: database migrations, security code, API contracts, breaking changes,
production deployments, legal/compliance work. The ultraplan step identifies
risks before ultrathink reasons through the approach.

**VERIFY** (novel claims, metrics, proofs):
```
hypothesis -> broly(CLUSTER: gather evidence)
           -> broly(MESH: council + devil's advocate deliberation)
           -> implement based on verdict
           -> falsify-self(claim)
```
Used for: convergence proofs, benchmark claims, performance metrics,
statistical results, any claim that would go in a paper or whitepaper.
The devil's advocate role specifically challenges the hypothesis.

#### Effort for Dispatched Agents (Claude Code 2.1.292+)

When a wave's strategy hands work to a sub-agent via the `Agent` tool, carry
the precision level straight into its `effort` parameter — Claude Code
2.1.292 added `effort` to the `Agent` tool so a dispatched sub-agent runs at
the level the dispatcher asks for, instead of a fixed default:

| Precision | Agent tool `effort` | Why |
|-----------|---------------------|-----|
| FAST | `low` | Direct edit -> test -> ship; no deliberation to pay for |
| CAREFUL | `high` | Sensitive surgery earns the deeper reasoning pass |
| VERIFY | `xhigh` | Council + devil's advocate + falsification is the expensive end |

This is a per-dispatch call argument, not this skill's own frontmatter
`effort:` key (that one governs how `/ultrado` itself is invoked, not how it
dispatches). `skills/dispatch/SKILL.md` and `rules/agent-dispatch-protocol.md`
carry the general form of this mapping for any skill that dispatches agents.

> **Caveat (2026-10-10): `CLAUDE_CODE_EFFORT_LEVEL` overrides per-dispatch
> effort.** Claude Code's [subagent effort
> documentation](https://code.claude.com/docs/en/sub-agents#choose-an-effort-level)
> states that this environment variable takes precedence over both the
> subagent definition's `effort` field and the per-invocation parameter.
> GRIP's shipped `settings.json` pins `CLAUDE_CODE_EFFORT_LEVEL=max`
> (enforced by `scripts/grip-health-check.sh`), so the FAST→`low` and
> CAREFUL→`high` mappings in the table above are currently overridden —
> every dispatched agent runs at `max`. This is a known conflict
> (`CLAUDE.md` § "Effort and a newly released model"); V>> has not yet
> decided which should win. Until the health-check requirement is relaxed
> or the variable is cleared, the per-dispatch mapping documents the
> *intended* behaviour but does not take effect in practice.

#### Recursive Depth (Full-Converge Pattern)

Each wave decomposes into tasks and atoms, each with their own converge loop.
Depth budget flows downward to prevent explosion:

```
Wave (depth = fibonacci(wave_num))
  +-- Task (depth = min(3, parent_remaining_depth))
       +-- Atom (depth = min(2, parent_remaining_depth))
            +-- LEAF (no further recursion)
```

| Level | Max Depth | Criterion | Example |
|-------|-----------|-----------|---------|
| Wave | fibonacci(N) | Wave hypothesis resolved | "W3 provenance complete" |
| Task | 3 | Task hypothesis resolved | "InsightEntry has platform" |
| Atom | 2 | Atom hypothesis resolved | "Regex extracts IDs" |

**Total depth cap**: Sum of all nested depths <= 20 per wave.

Each level inherits its hypothesis from the parent:
- Wave: "W3 adds provenance tags to guild insights"
- Task: "guild_learner.py stores platform field" (sub-hypothesis)
- Atom: "extract_platform() returns correct values for all 4 platforms"

#### Falsification as Universal Criterion

Every converge loop exits on hypothesis resolution:

```python
def hypothesis_resolved(hypothesis, state):
    """The universal criterion. Exit when resolved either way."""
    evidence = gather_evidence(state)
    
    if evidence.confirms(hypothesis):
        hypothesis.mark_confirmed(evidence=evidence)
        return True   # Confirmed -- move on
    
    if evidence.falsifies(hypothesis):
        hypothesis.mark_falsified(evidence=evidence)
        return True   # Falsified -- ALSO a valid exit (learned something)
    
    return False      # Inconclusive -- keep improving
```

This replaces converge's score-based criterion (>= 93). Score-based exit
suffers from the 91-attractor. Falsification-based exit forces clarity.

**Both confirmed and falsified are successful exits.** A falsified hypothesis
means we learned something -- the task pivots based on what we learned, it
doesn't retry the same approach.

#### Broly Devil's Advocate Protocol

When precision is VERIFY, the broly council deliberates:

1. **Evidence phase** (CLUSTER power level):
   - Analyst: gather data supporting the hypothesis
   - Explorer: search for counterexamples
   - Reviewer: assess evidence quality

2. **Deliberation phase** (MESH power level):
   - Pragmatist: "Does this actually matter?"
   - Devil's Advocate: "Here's why this is wrong..."
   - Integrator: "The evidence suggests..."

3. **Verdict**: proceed / rethink / abandon
   - Proceed: implement as planned
   - Rethink: redesign based on concerns
   - Abandon: mark hypothesis falsified, pivot

The devil's advocate is NOT optional in VERIFY mode. Its role is to find
the weakest part of the hypothesis and attack it. If the hypothesis survives,
it's stronger. If it doesn't, we saved time.

### Phase 3: SHIP (after each wave)

```
git add [specific files]
git commit -m "type: description"
git push
gh pr create (if feature branch)
gh pr merge --admin (if CI passes)
git pull (after merge)
```

No stopping for confirmation on feature branches.
Protected branches: ask first.

### Phase 4: MEASURE (after each wave)

1. **Hypothesis verification**: check wave hypothesis status
   - Log: CONFIRMED / FALSIFIED / INCONCLUSIVE (with evidence)
2. Run tests (`npm test` / `pytest` as appropriate)
3. Anti-drift output (Rule 11 tight 4-line template — drop "Done" when
   commit message says it; "Remaining/Open/Next" always-on)
4. Context gate check (85% = HALT and serialise)

### Phase 5: LEARN (between waves)

1. Feed completed work into insights pipeline
2. Genome evolution step (if genome exists)
3. Update instance `next_up` field
4. Record hypothesis outcome in hypothesis engine

### Phase 6: CHECKPOINT (crash resilience)

**After every wave**, save state via `lib/ultrado_checkpoint.py`:

```python
from lib.ultrado_checkpoint import checkpoint_wave

checkpoint_wave(
    run_id=run_id,
    wave_num=wave_num,
    goal=goal,
    plan_file=plan_path,
    completed_tasks=done_tasks,
    pending_tasks=remaining_tasks,
    hypotheses=registered_hypotheses,
    precision_levels=wave_precisions,
    wave_results={"confirmed": confirmed, "falsified": falsified},
)
```

This ensures that if the session crashes (lembed SIGSEGV, context overflow,
network timeout, macOS sleep), the next `/ultrado` invocation can resume
from the last completed wave instead of starting from scratch.

Checkpoints expire after 48 hours (stale state is worse than fresh start).

### Phase 7: PERSIST (at session end or context gate)

```
/save  -- serialise all GRIP state
```

Instance DNA, KONO memories, session context, active plan.

### Phase 8: BREATHE (the InfiniteMachine)

At context gate (85%):
1. HALT all work
2. Serialise everything (checkpoint + /save)
3. Ask: compact (summarise and continue) or fresh (new session with context)
4. If compact: compress prior context, continue from last wave
5. If fresh: hand off via session-context.md + checkpoint

**The loop never terminates.** It either completes the goal, compacts and continues,
or hands off to a fresh session. There is no "I ran out of context" failure mode.

### Native Stop-hook continuation (CC 2.1.163)

The BREATHE + no-yield loop is now natively reinforceable: a Stop / SubagentStop
hook can return `hookSpecificOutput.additionalContext` to feed the next wave's
context back into the model AND keep the turn going — without the hook-error label
older CC raised (CC 2.1.163). This mechanises `rsi-loop-no-yield.md` at the harness
layer: the loop no longer leans on prose self-discipline to avoid a spurious yield
between waves — the Stop hook re-injects the wave plan and continues. GRIP keeps the
exogenous exit criterion (CI-green + AGORA-PASS); the native additionalContext only
removes the *accidental* stop, never the real gate.

## Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| goal | auto-resume | What to achieve (or continue remaining work) |
| duration | unlimited | Maximum wall time (optional cap) |
| depth | auto (Fibonacci) | Max convergence depth per wave |
| --no-ship | false | Skip auto-ship (dry run mode) |
| --no-hypothesis | false | Skip hypothesis registration |
| --waves N | 6 | Maximum number of Fibonacci waves |
| --resume | auto | Continue from last checkpoint |
| --fast | false | Force FAST precision for all tasks |
| --careful | false | Force CAREFUL precision for all tasks |
| --verify | false | Force VERIFY precision for all tasks |

## Smart Resume

When invoked without arguments:

```
/ultrado
```

1. Check `state/ultrado/` for crash checkpoint -> resume from last wave
2. Read `rules/session-context.md` for remaining work
3. Read `plans/*.md` for active plans
4. Continue from the last incomplete wave
5. If nothing remains: "All caught up. What's next?"

This makes `/ultrado` the default session opener. Start every session with it.

## Execution Protocol

When `/ultrado` is invoked, execute this sequence WITHOUT stopping for confirmation:

```python
def ultrado(goal=None, duration=None, depth='auto', waves=6, precision_override=None):
    from lib.ultrado_checkpoint import checkpoint_wave, resume_from_checkpoint
    from lib.ultrado_precision import (
        detect_precision, PrecisionLevel,
        adapt_wave_plan, WAVE_COST_P50, WAVE_COST_P90,
    )
    from lib.hypothesis_engine import register, verify_all
    
    # ABSORB
    checkpoint = resume_from_checkpoint()
    if checkpoint:
        goal = checkpoint['goal']
        start_wave = checkpoint['wave_num'] + 1
        # Report: "Resuming from W{N} checkpoint ({timestamp})"
    else:
        start_wave = 1
        context = recall_session_state()
        goal = goal or context.remaining_work or ask_user("What should I work on?")
    
    # ADAPT WAVE PLAN TO CONTEXT BUDGET (GRIP#2295 + H090)
    cost = WAVE_COST_P90 if _goal_needs_verify(goal) else WAVE_COST_P50
    wave_plan = adapt_wave_plan(
        current_context_pct=report_context_pct(),
        total_budget=context_total_budget(),
        max_waves=waves,
        wave_cost=cost,
    )
    print(f"[ultrado] {wave_plan.reason}")
    if wave_plan.handoff:
        save_all_state()
        return "handoff"
    waves = wave_plan.waves  # adapted count flows into PLAN + EXECUTE
    
    # PLAN
    plan = ultraplan(goal, criterion='hypothesis-driven', max_waves=waves)
    for wave in plan.waves:
        register(
            claim=wave.hypothesis,
            metric=wave.metric,
            prediction=wave.prediction,
            deadline=wave.deadline,
        )
        wave.precision = detect_precision(wave.description, precision_override)
    
    # EXECUTE (Fibonacci waves with recursive convergence)
    for wave_num in range(start_wave, waves + 1):
        if context_gate_exceeded(0.85):
            checkpoint_wave(run_id, wave_num - 1, goal, ...)
            serialise_and_offer_compact()
            break
        
        wave = plan.waves[wave_num]
        fib_depth = fibonacci(wave_num)
        
        # RECURSIVE CONVERGENCE with adaptive precision
        converge(
            state=wave.tasks,
            criterion=lambda s: hypothesis_resolved(wave.hypothesis, s),
            improve=adaptive_improve(wave.precision),
            max_depth=fib_depth,
        )
        
        # SHIP
        auto_ship()
        
        # MEASURE
        verify_hypothesis(wave.hypothesis)
        run_tests()
        anti_drift_output()  # tight 4-line: Done?/Remaining/Open/Next (Rule 11)
        
        # LEARN
        evolve_genome()
        update_instance_next_up()
        
        # CHECKPOINT
        checkpoint_wave(
            run_id=run_id, wave_num=wave_num, goal=goal,
            plan_file=plan.path,
            completed_tasks=plan.completed_tasks(),
            pending_tasks=plan.pending_tasks(),
        )
    
    # PERSIST
    save_all_state()
    clear_checkpoint(run_id)
    output_final_summary()
```

## Anti-Patterns

- Do NOT flatten Fibonacci waves (escalating depth is the point)
- Do NOT skip breathing pauses (context gate protection)
- Do NOT self-score novel claims (VERIFY mode exists for this)
- Do NOT skip hypothesis registration (every wave is an experiment)
- Do NOT skip checkpoints (today's crash cascade proves why)
- Do NOT post automated updates to Guild Discord #general
- Do NOT stop at analysis -- every phase produces a deliverable
- Do NOT ask for permission on feature branch pushes
- Do NOT re-read files already in mental model (GRIP-First Tier 0)
- Do NOT use score-based criterion (>= 93) -- use hypothesis resolution
- Do NOT spawn broly for FAST tasks (token waste)
- Do NOT recurse deeper than the depth budget allows (cap at 20 total)

## Fibonacci Wave Table

| Wave | Batch | Depth | Precision Default | Character |
|------|-------|-------|-------------------|-----------|
| W1 | 1 | 1 | FAST | Get something working |
| W2 | 1 | 1 | FAST | Refine the foundation |
| W3 | 2 | 2 | auto-detect | Add substance |
| W4 | 3 | 3 | auto-detect | Rich features |
| W5 | 5 | 5 | auto-detect | Polish + edge cases |
| W6 | 8 | 8 | auto-detect | Intelligence layer |

## Backward Compatibility

```python
# /converge "fix the auth" becomes:
ultrado(goal="fix the auth", waves=1, precision_override=None)

# /full-converge "refactor the whole module" becomes:
ultrado(goal="refactor the whole module", waves=6, precision_override=None)
```

Existing converge flags (--depth, --criterion, --show-work, --code) are
translated to ultrado equivalents. The converge scoring rubric, genome
tracking, and metrics recording all still work -- they're the kernel.

## Relationship to Other Commands

```
/ultrado
|-- /recall (Phase 0: ABSORB)
|-- /ultraplan (Phase 1: PLAN)
|-- converge() kernel (Phase 2: EXECUTE)
|   |-- /hypothesis-driven-development (per-loop criterion)
|   |-- /falsify-self (VERIFY mode principle)
|   |-- /broly (VERIFY mode council)
|   +-- /full-converge (recursive depth pattern)
|-- /auto-ship (Phase 3: SHIP)
|-- /learn (Phase 5: LEARN)
|-- /save (Phase 7: PERSIST)
+-- InfiniteMachine (Phase 8: BREATHE -- never stop)
```

`/ultrado` is the fixed point. Running `/ultrado` inside `/ultrado` is a no-op --
it's already doing everything.

## Why This Exists

V>>'s observed optimal pattern:

> "Every good session follows the same shape: recall what I know, plan what
> to do, execute in escalating waves, ship after each wave, measure what
> happened, learn from it, save everything. /ultrado is that shape, codified.
> The name means 'ultra-do' -- not ultra-think, not ultra-plan, but ultra-DO.
> Thinking and planning are subsets of doing."

v2.0 addition (Gen 499):

> "What we're missing is the recursive, all the way down recurse converge
> improve. Criterion for all loops is: falsification. A balanced approach
> between speed and deliberate, pragmatic, measured approach."

## Falsification of This Command

This command claims: "Recursive falsification-driven convergence produces better
outcomes than linear Fibonacci wave execution."

Falsified by:
- Integration test (provenance W3-W6) takes longer than v1 approach
- Hypothesis pre-registration adds ceremony without catching real issues
- Crash recovery introduces its own bugs (checkpoint corruption, stale state)
- Broly council adds 30+ minutes per VERIFY task with no quality improvement
- Recursive depth causes explosion (total work exceeds depth cap of 20)
