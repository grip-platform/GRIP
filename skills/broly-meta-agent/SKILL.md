---
name: broly-meta-agent
description: Recursive meta-agent protocol with 3-level fractal architecture, multi-framework reasoning, four memory types, and evolutionary power scaling. Use when invoking /broly or running deep multi-agent adversarial reasoning.
---

# Broly Meta-Agent Protocol

Recursive meta-agent with fractal architecture, multi-framework reasoning, 4-type memory, and evolutionary power scaling.

"Fractal" here means the same dispatch pattern repeated at each level. There are at most three levels: the lead, a teammate, and a teammate's subagent (see Safety Constraints). It is a layered design with a fixed depth, not an unbounded one. A teammate is a separate Claude Code session, not a subagent, so its own subagent is depth 1 of that session; this stays within the "depth 0 and 1 only" limit in `agents/CLAUDE.md`.

## Concept Attribution

Concept: a collaborator (compressed information language specification)
Implementation: GRIP framework

## When To Use

- Complex tasks requiring multiple perspectives
- Tasks that benefit from parallel analysis
- Architectural decisions needing council deliberation
- Research requiring depth + breadth simultaneously
- Any task where single-agent reasoning is insufficient

## Architecture

### Power Level System

Power levels control ALL scaling dimensions simultaneously:

```text
THREAD ──> FORK ──> CLUSTER ──> PIPELINE ──> MESH
 1 agent    2-3      5-7        7-10        10-15
 CoT        +ToT     +GoT       +BoT        +Meta
 STM        +LTM     +Epis      +Sem        +Evol
 None       Task     Team       Team        Team
            tool     swarm      pipe        council
```

### Memory Types

| Type | Backend | Persistence | Available |
|------|---------|-------------|-----------|
| STM | Python dict | Session | All |
| LTM | MEMORY.md + EmbeddingEngine | Permanent | fork+ |
| Episodic | history-search.py + SemanticCompressor | Cross-session | cluster+ |
| Semantic | EmbeddingEngine.search_hybrid + KONO | Permanent | cluster+ |

### Reasoning Frameworks

| Framework | Pattern | Best For |
|-----------|---------|----------|
| CoT | A -> B -> C -> conclusion | Linear problems |
| ToT | Branch and evaluate | Comparing options |
| GoT | Graph traversal | Relationship mapping |
| BoT | Independent + merge | Creative brainstorming |
| SelfAsk | Recursive decomposition | Complex questions |
| ReAct | Think-Act-Observe loop | Investigation/debugging |
| Reflexion | Post-mortem analysis | Learning from execution |

### Framework Auto-Selection

The ReasoningRouter analyzes task keywords to select frameworks:

- "compare", "options", "trade-off" -> ToT
- "related to", "impact", "cascade" -> GoT
- "brainstorm", "all angles", "creative" -> BoT
- "break down", "prerequisite" -> SelfAsk
- "investigate", "debug", "verify" -> ReAct
- "what went wrong", "lesson learned" -> Reflexion
- Default -> CoT

## Agent Teams Integration

### Team Patterns (CLUSTER+)

**Swarm (CLUSTER)**: All teammates work independently on subtasks, results merged by lead.

Roles: analyst, implementer, reviewer, explorer, documenter

**Pipeline (PIPELINE)**: Sequential stages where each output feeds the next.

Stages: researcher -> architect -> implementer(s) -> tester -> reviewer

**Council (MESH)**: Specialist personas provide independent analyses, then deliberate.

Roles: pragmatist, theorist, security-advisor, ux-advocate, devil-advocate, integrator

### Teammate Context

Each teammate receives a generated CLAUDE.md with:
- Awareness of team pattern and their role
- Coordination rules (shared task list usage)
- The parent task description
- All inherited GRIP safety gates

### Graceful Degradation

```text
Agent Teams env var missing? -> Fall back to Task tool
KONO unavailable?            -> Pure hybrid search
Embeddings DB missing?       -> MEMORY.md text search
Coherence checker missing?   -> Skip coherence gate
```

## Memory Consolidation

STM entries accessed 3+ times are promoted to LTM if they pass:
1. Coherence gate (is the content meaningful English?)
2. LTM dual-write (MEMORY.md + embeddings)

## Safety Constraints

- Max recursion depth: 3 (lead -> teammate -> Task subagent)
- Max concurrent agents: 15 (MESH cap, enforced by TeammateTool)
- Token budgets enforced per power level
- All GRIP safety gates active in all agents
- MESH evolution proposes changes via AskUserQuestion only

## Package Structure

```text
~/.claude/lib/broly/
    __init__.py          Package exports
    power_levels.py      PowerLevel enum, gating, team config
    orchestrator.py      Main BrolyOrchestrator
    reasoning.py         ReasoningRouter + 7 frameworks
    memory.py            BrolyMemory unified router
    stm.py               ShortTermMemory
    ltm.py               LongTermMemory
    episodic.py          EpisodicMemory
    semantic.py          SemanticMemory
    consolidator.py      STM-to-LTM promotion
    team_config.py       Agent Team configuration
```

## CLI

```bash
# Via /broly command
/broly auto <task>
/broly fork <task>
/broly cluster <task>
/broly status

# Via grip CLI
grip broly status
grip broly execute --level cluster "Analyze security"

# Direct Python
python3 ~/.claude/lib/broly/orchestrator.py execute "task" --level fork
python3 ~/.claude/lib/broly/orchestrator.py status
python3 ~/.claude/lib/broly/orchestrator.py assess "task" --level cluster
```

## Context Requirements

| Level | Recommended Context | Notes |
|-------|-------------------|-------|
| THREAD | 1M (default) | Standard context sufficient |
| FORK | 1M (default) | Standard context sufficient |
| CLUSTER | 1M (default) | Full context recommended |
| PIPELINE | 1M (default) | Full context required |
| MESH | 1M (default) | Full context required |

Use `/extend-context` before invoking PIPELINE or MESH levels.

## Per-agent LLM assignment (CLUSTER / MESH)

In CLUSTER and MESH modes, each role can run on a **different** LLM. Diversity
is the point — a single-family council shares blind spots; a council that mixes
Gemini + xAI + DeepSeek + Groq has independent error modes without CCH usage.

`lib/broly_council.py` exposes `BrolyRoleAssignment(role, model, weight)`. Defaults:

| Role | Default model | Weight | Why |
|---|---|---|---|
| analyst | gemini/gemini-2.5-flash | 1.0 | Fast independent analysis |
| explorer | xai/grok-4.20-0309-non-reasoning | 1.0 | Different family → independent search bias |
| reviewer | deepseek/deepseek-v4-pro | 1.5 | Independent technical review |
| pragmatist | groq/openai/gpt-oss-120b | 1.0 | Fast, cheap "does it matter?" check |
| devil | xai/grok-4.20-0309-non-reasoning | 2.0 | Devil's case weighted heavily — surface objections |
| integrator | gemini/gemini-2.5-flash | 1.0 | Cross-role synthesis |

Override per operator at `~/.claude/config/broly-roles.json` (template at
`config/broly-roles.json.example`). The diversity guard test
(`test_default_assignments_are_diverse` in `tests/test_broly_council.py`)
fails if defaults collapse to a single provider family — preventing accidental
homogenisation.

## Criterion mesh (`/broly-auto --criterion-mesh`)

Shipped via PR titled "feat: broly per-agent LLM assignment + criterion-mesh
(LLM council verdict)". The convergence criterion is itself a multi-LLM vote:

```
evaluate_criterion_mesh(claim, state, assignments) -> {
    "verdict": "met" | "unmet" | "inconclusive",
    "weighted_met_score": 0.0..1.0,
    "per_role": [{"role": ..., "model": ..., "vote": ..., "confidence": ...}, ...],
    "devils_veto": bool,
    "rationale": "...",
}
```

Aggregation rules:
- **Devil's veto**: `devil` votes `unmet` with confidence > 0.85 → verdict is
  `unmet` regardless of majority.
- Else weighted score: `Σ(weight × confidence × verdict_score) / Σ(weight)`,
  with `verdict_score` mapping `{met: 1.0, inconclusive: 0.5, unmet: 0.0}`.
- Verdict bands: `met` if score ≥ 0.66, `unmet` if ≤ 0.34, else `inconclusive`.

### Recommended invocation: the kernel-backed idempotent wrapper

`lib/broly_ultracode.py::evaluate_criterion_mesh_idempotent(claim, state,
assignments)` is the production entrypoint — same mesh, same verdict contract,
plus the `lib/ultracode` kernel underneath (GRIP consumer #1 of the graduated
kernel; hypothesis H769):

- **Resume**: a council killed mid-run (crash, transient 429) resumes free —
  completed role legs replay from the persisted per-run cache
  (`state/broly/ultracode-runs/<run_key>.json`) with zero new provider calls.
- **Fresh-by-default**: a COMPLETED run is never silently re-served; the same
  question re-samples fresh voices unless `replay=True` is passed explicitly
  (the stale-cache guard H769 tracks — target 0 incidents).
- **Monotone floor**: the verdict passes a deterministic shape check
  (`verdict_shape_floor` via the kernel's `verify`) before it is served.
- **Telemetry**: `{calls, cache_hits, resumed, replay}` per run appended to
  `logs/ultracode-consumers.jsonl` and returned under `result["_ultracode"]`.

Call `evaluate_criterion_mesh` directly only when you deliberately want no
run-file side effects (pure in-memory evaluation, e.g. inside another cache).

### Hypothesis (registered with this protocol)

> The criterion-mesh produces fewer false-positive convergence verdicts than
> single-model criteria, because a single model's blind spot can be caught by
> another model in the council. Falsified if (a) > 70% of `met` verdicts under
> criterion-mesh would also have been `met` under single-model evaluation
> (no diversity benefit), or (b) the devil's veto fires more often than 30%
> of runs (over-cautious, blocks legitimate progress), or (c) operators
> consistently override mesh verdicts (no trust in the algorithm).

Track via the broly run log over the first 20 mesh-mode invocations.

## Status

- Per-agent assignment + criterion-mesh API: **shipped** (`lib/broly_council.py`,
  10 hermetic tests).
- Live HAL multi-provider dispatch wiring: **shipped** — `dispatch_role` calls
  `_query_grip_infer` (HAL Pool B) with the econ-default non-Anthropic fallback
  and the CCH billing guard; the earlier "raises `NotImplementedError` outside
  stub mode" note described a pre-wiring state that no longer exists.
- Kernel-backed idempotent wrapper: **shipped** (`lib/broly_ultracode.py`,
  hermetic tests in `tests/test_broly_ultracode.py`) — resume + fresh-by-default
  + replay semantics over `lib/ultracode` (H769 consumer #1).
CLUSTER will warn but proceed without 1M context.
