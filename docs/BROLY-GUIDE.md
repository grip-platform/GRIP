# Broly Guide

The recursive meta-agent that scales from solo reasoning to multi-agent councils.

## What Is Broly?

Broly is GRIP's power-scaling system. It lets the agent spawn additional agents to tackle complex tasks, each with specialised roles and reasoning frameworks.

At its simplest (THREAD), it's a single agent thinking step-by-step. At its most powerful (MESH), it's a 15-agent council with independent analysis, voting, and dissent tracking.

## When and How to Use Broly

### Basic Syntax

```bash
/broly <level> <task description>
```

The task description is a natural language prompt. Be specific about what you want done - Broly uses it to select reasoning frameworks, decompose work, and assign roles.

### THREAD - Solo Reasoning

For simple tasks where one agent is enough.

```bash
/broly thread Fix the off-by-one error in the pagination component

/broly thread Explain how the authentication middleware works

/broly thread Add a loading spinner to the submit button
```

### FORK - Parallel Exploration (2-3 Agents)

For tasks that benefit from doing two things at once.

```bash
/broly fork Add input validation to the signup form and write unit tests for it

/broly fork Research how Stripe webhooks work and draft an implementation plan

/broly fork Fix the CSS layout bug on mobile while also updating the desktop nav
```

### CLUSTER - Swarm Team (5-7 Agents)

For complex work spanning many files. Spawns a team with analyst, implementer, reviewer, explorer, and documenter roles.

```bash
/broly cluster Refactor the entire authentication system from session-based to JWT tokens

/broly cluster Implement a real-time notification system with WebSocket support, database schema, API endpoints, and frontend components

/broly cluster Migrate the codebase from JavaScript to TypeScript including all test files
```

### PIPELINE - Sequential Stages (7-10 Agents)

For multi-phase projects where each stage feeds the next. Requires `/extend-context` first.

```bash
/broly pipeline Research best practices for rate limiting APIs, design the architecture, implement the middleware, add Redis caching, and write integration tests

/broly pipeline Audit the current database schema for performance issues, design an optimised schema, write the migration scripts, update all affected queries, and verify with load tests

/broly pipeline Investigate why the checkout flow has a 15% drop-off rate, identify UX issues, design improvements, implement the changes, and set up A/B testing
```

### MESH - Council Decision (10-15 Agents)

For high-stakes decisions needing multiple expert perspectives. Requires `/extend-context` first. Spawns a council (pragmatist, theorist, security-advisor, ux-advocate, devil's-advocate, integrator) that analyses independently, then votes and synthesises with recorded dissent.

```bash
/broly mesh Evaluate whether we should migrate from REST to GraphQL for our public API - consider developer experience, performance, security, and migration cost

/broly mesh Design the architecture for a multi-tenant SaaS platform that needs to support 10k concurrent users with data isolation, audit logging, and SOC2 compliance

/broly mesh Assess whether to build our own auth system or use Auth0/Clerk - factor in cost at scale, vendor lock-in, customisation needs, and security posture
```

### Auto Mode

Let Broly pick the level based on the active mode's configuration:

```bash
/broly auto Implement the feature described in ticket PROJ-142
```

Starts at THREAD and escalates if the mode config has matching escalation signals.

### Real-World Examples

| What You're Doing | Command |
|-------------------|---------|
| Fix a typo | No Broly needed |
| Add a new API endpoint | `/broly fork Add a GET /api/users/:id endpoint with validation and tests` |
| Rewrite the billing module | `/broly cluster Rewrite the billing module to support monthly and annual plans with proration` |
| Build a feature end-to-end | `/broly pipeline Research Stripe Connect, design the payout flow, implement sender and receiver, add webhook handling, write E2E tests` |
| Choose a database | `/broly mesh Evaluate PostgreSQL vs CockroachDB vs PlanetScale for our multi-region write-heavy workload` |

**Rule of thumb**: If you'd naturally want a second opinion or need to hold multiple perspectives simultaneously, escalate.

## The 5 Power Levels

```
THREAD ──> FORK ──> CLUSTER ──> PIPELINE ──> MESH
  1 agent   2-3       5-7        7-10        10-15
  solo      parallel  swarm      sequential  council
```

| Level | Agents | Mechanism | Memory | Context | Reasoning |
|-------|--------|-----------|--------|---------|-----------|
| THREAD | 1 | Solo | STM only | 1M | CoT |
| FORK | 2-3 | Task tool | STM + LTM | 1M | CoT, ToT |
| CLUSTER | 5-7 | Agent Team (swarm) | All 4 types | 1M recommended | + GoT |
| PIPELINE | 7-10 | Agent Team (pipeline) | All 4 types | 1M required | + BoT, SelfAsk, ReAct |
| MESH | 10-15 | Agent Team (council) | All 4 types | 1M required | All 7 + Reflexion |

### Prerequisites

| Level | Requirements |
|-------|-------------|
| THREAD, FORK | None (works out of the box) |
| CLUSTER | Agent Teams recommended |
| PIPELINE, MESH | Agent Teams + extended context |

Without Agent Teams enabled, CLUSTER+ falls back to Task tool subagents with reduced agent count. Memory and reasoning still operate at the requested level.

### Enabling Agent Teams

Agent Teams is a Claude Code experimental feature that allows Broly to spawn coordinated multi-agent teams (swarm, pipeline, council patterns). Without it, CLUSTER+ levels use individual Task tool calls instead.

Set the environment variable before launching Claude Code:

```bash
# Add to your shell profile (~/.zshrc or ~/.bashrc)
export CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1
```

Then restart your terminal or run `source ~/.zshrc`.

Alternatively, set it for a single session:

```bash
CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1 claude
```

### Extending Context for PIPELINE and MESH

**Note**: Since March 2026, GRIP defaults to **Opus 4.6 with a 1M context window**. All Broly levels (including PIPELINE and MESH) work out of the box without needing `/extend-context`.

The `/extend-context` command is now legacy. It can still be used to switch to **Sonnet 1M** for cheaper per-token cost at the expense of reasoning depth:

| | Opus 1M (default) | Sonnet 1M (via /extend-context) |
|---|---|---|
| Context window | 1M tokens | 1M tokens |
| Reasoning | Strongest | Strong |
| Speed | Standard | Faster |
| Broly levels | All levels | All levels |
| Best for | All tasks, deep reasoning | Cost-sensitive batch work |

**Recommendation**: Stay on Opus 1M for all work. Only switch to Sonnet if you need to optimise for cost on large batch operations.

## Auto-Activation via Modes

15 of 25 GRIP modes have Broly auto-active. When you activate a mode, Broly engages at the mode's default level without needing `/broly`:

| Mode Default | Modes |
|-------------|-------|
| FORK | code, automation, legal, business, brainstorm, review |
| CLUSTER | research, synthesis, analysis, testing, security |
| PIPELINE | architect, planning |
| MESH | strategy, pair |

Each mode also defines escalation and de-escalation signals:

```
/mode code   --> Broly auto-active at FORK
               - Escalates to CLUSTER for refactors spanning 5+ files
               - De-escalates to THREAD for single-file fixes
```

## 7 Reasoning Frameworks

Broly selects a reasoning framework based on task keywords (or mode hint):

| Framework | How It Thinks | Best For |
|-----------|--------------|----------|
| **Chain-of-thought (CoT)** | Step-by-step sequential | Default, general tasks |
| **Tree-of-thought (ToT)** | Explore parallel branches, evaluate | Comparing options, trade-offs |
| **Graph-of-thought (GoT)** | Non-linear concept traversal | Impact analysis, dependencies |
| **Bag-of-thought (BoT)** | Independent parallel analysis, scored | Brainstorming, comprehensive coverage |
| **SelfAsk** | Recursive question decomposition | Breaking down complex problems |
| **ReAct** | Reason -> Act -> Observe loop | Debugging, investigation, verification |
| **Reflexion** | Post-execution reflection | Retrospectives, learning from mistakes |

**Auto-selection keywords**:

| If task contains... | Framework selected |
|--------------------|--------------------|
| "compare", "alternatives", "trade-off" | Tree-of-thought |
| "depends on", "impact", "cascade" | Graph-of-thought |
| "brainstorm", "all angles", "comprehensive" | Bag-of-thought |
| "break down", "decompose", "step by step" | SelfAsk |
| "debug", "investigate", "verify" | ReAct |
| "what went wrong", "improve", "retrospective" | Reflexion |
| (no match) | Chain-of-thought |

## 4-Type Memory System

Memory access scales with power level:

| Memory Type | Available At | What It Stores |
|-------------|-------------|---------------|
| **STM** (Short-Term) | All levels | Current session key-value pairs |
| **LTM** (Long-Term) | FORK+ | Cross-session patterns via MEMORY.md + embeddings |
| **Episodic** | CLUSTER+ | Session history search (JSONL transcripts) |
| **Semantic** | CLUSTER+ | Concept-level hybrid search via KONO spatial memory |

**Memory consolidation**: When an STM entry is recalled 3+ times and passes a coherence gate, it promotes to LTM automatically. This is how short-term learnings become permanent cross-session knowledge.

**Power gating is mechanical**: Attempting to access episodic memory at THREAD level raises an error. No workarounds.

## Team Patterns Explained

### Swarm (CLUSTER)

Everyone works independently in parallel. Results merge at the end.

```
Lead assigns tasks
  |-- analyst: reads codebase, identifies patterns
  |-- implementer: writes code changes
  |-- reviewer: reviews the implementer's work
  |-- explorer: finds related files (uses haiku for speed)
  |-- documenter: updates docs (uses haiku for speed)
  |
  v
Lead merges all results
```

### Pipeline (PIPELINE)

Work flows sequentially. Each stage's output feeds the next.

```
researcher --> architect --> implementer-1 --> implementer-2 --> tester --> reviewer
                                    |
                         quality gate between each stage
```

### Council (MESH)

Specialists analyse independently, then an integrator synthesises. Dissenting opinions are preserved.

```
Phase 1: Independent Analysis
  pragmatist, theorist, security-advisor, ux-advocate, devil-advocate
                           |
Phase 2: Integration
                     integrator
                           |
                 Final Decision + Dissent Notes
```

## Configuration

### In Mode YAML

```yaml
broly:
  enabled: true
  default_level: fork
  reasoning_hint: cot
  escalation:
    to: cluster
    signals:
      - "complex multi-file refactor"
      - "security audit"
  deescalation:
    to: thread
    signals:
      - "single-file fix"
      - "quick question"
```

### Limits

| Setting | Value |
|---------|-------|
| Max recursion depth | 3 (lead -> teammate -> subagent) |
| Token multiplier (FORK) | 2x |
| Token multiplier (CLUSTER+) | 5x |

## Quick Decision Guide

```
Is this a simple, single-file task?
  YES --> Don't use Broly (or THREAD)
  NO  --> Continue

Does it need parallel exploration?
  YES --> FORK

Does it span 5+ files or need specialised roles?
  YES --> CLUSTER

Does it have sequential phases (research -> design -> build)?
  YES --> PIPELINE

Is it a high-stakes decision needing multiple perspectives?
  YES --> MESH
```

## Further Reading

- [Broly Architecture](BROLY-ARCHITECTURE.md) - Module map, orchestrator pipeline, full technical details
- [Modes Guide](MODES-AND-SKILLS-GUIDE.md) - Which modes auto-activate Broly and at what level
