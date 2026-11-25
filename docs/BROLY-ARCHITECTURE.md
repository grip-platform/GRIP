# Broly Architecture

Recursive meta-agent with fractal architecture, multi-framework reasoning, 4-type memory, and evolutionary power scaling.

**Version**: 1.0.0
**Source**: `lib/broly/` (2,355 lines across 11 modules)

---

## Overview

Broly scales from single-session reasoning to multi-agent councils via Claude Code's Agent Teams. Each power level unlocks additional reasoning frameworks, memory types, and team patterns.

```
Task Input
    |
    v
[Orchestrator] -- assess -> [Power Level Resolution]
    |                              |
    v                              v
[Memory Retrieval]         [Reasoning Router]
    |                              |
    v                              v
[Context Enrichment]       [Prompt Generation]
    |                              |
    +--------- merge ------------->+
                |
                v
        [Execution Engine]
        /       |        \
   THREAD    TASK_TOOL   AGENT_TEAM
   (solo)    (fork)      (cluster+)
                |
                v
        [Post-Execution]
        - STM storage
        - Memory consolidation (STM -> LTM)
        - Execution logging
```

---

## Power Levels

| Level | Agents | Mechanism | Team Pattern | Reasoning | Memory | Context |
|-------|--------|-----------|-------------|-----------|--------|---------|
| THREAD | 1 | None (solo) | -- | CoT | STM | 1M |
| FORK | 2-3 | Task tool | -- | CoT, ToT | STM, LTM | 1M |
| CLUSTER | 5-7 | Agent Team | Swarm | CoT, ToT, GoT | All 4 | 1M recommended |
| PIPELINE | 7-10 | Agent Team | Pipeline | CoT, ToT, GoT, BoT, SelfAsk, ReAct | All 4 | 1M required |
| MESH | 10-15 | Agent Team | Council | All 7 (+ Reflexion) | All 4 | 1M required |

### Power Level Selection

- **Explicit**: `/broly mesh "task description"`
- **Auto**: Defaults to THREAD, escalates based on mode config signals
- **Mode-driven**: Each GRIP mode can define a `broly.default_level` and `broly.escalation` config

### Legacy Aliases

| Old Name | Maps To |
|----------|---------|
| BASE | THREAD |
| SSJ | FORK |
| SSJ2 | CLUSTER |
| SSJ3 | PIPELINE |
| LSSJ | MESH |

---

## Module Map

| Module | Lines | Purpose |
|--------|-------|---------|
| `__init__.py` | 70 | Package exports, version |
| `orchestrator.py` | 546 | Core execute/spawn/merge loop |
| `power_levels.py` | 187 | Power level enum, configs, gating |
| `reasoning.py` | 362 | 7 reasoning framework prompt generators |
| `memory.py` | 220 | Unified memory router across 4 types |
| `stm.py` | 110 | Short-term memory (in-session dict) |
| `ltm.py` | 179 | Long-term memory (MEMORY.md + embeddings) |
| `episodic.py` | 149 | Session history search |
| `semantic.py` | 227 | Concept-level hybrid + KONO spatial |
| `consolidator.py` | 119 | STM-to-LTM promotion with coherence gate |
| `team_config.py` | 186 | Agent Team roles, patterns, task decomposition |

---

## Reasoning Frameworks

Each framework generates structured prompts that guide agent behavior. At higher power levels, some frameworks map to team patterns.

| Framework | Available At | Purpose | Auto-Select Keywords |
|-----------|-------------|---------|---------------------|
| **CoT** (Chain of Thought) | All levels | Sequential step-by-step reasoning | Default fallback |
| **ToT** (Tree of Thought) | FORK+ | Parallel branch exploration with evaluation | compare, options, alternatives, trade-off, pros and cons |
| **GoT** (Graph of Thought) | CLUSTER+ | Non-linear concept traversal | related to, connected, depends on, impact, cascade |
| **BoT** (Bag of Thought) | PIPELINE+ | Independent parallel analysis with scoring | brainstorm, creative, comprehensive, all angles |
| **SelfAsk** | PIPELINE+ | Recursive question decomposition | break down, decompose, prerequisite, step by step |
| **ReAct** | PIPELINE+ | Reason + Act + Observe loop | investigate, debug, find out, verify, test hypothesis |
| **Reflexion** | MESH only | Post-execution reflection and learning | what went wrong, improve, lesson learned, retrospective |

### Auto-Selection

The `ReasoningRouter.select_framework()` scores task descriptions against keyword signals. The framework with the highest keyword match count wins. If no keywords match, defaults to CoT.

Mode configs can override auto-selection via `broly.reasoning_hint`.

---

## Memory System

### 4 Memory Types

| Type | Scope | Backend | Power Level |
|------|-------|---------|------------|
| **STM** (Short-Term) | Current session | In-memory dict | All |
| **LTM** (Long-Term) | Cross-session | MEMORY.md + embeddings | FORK+ |
| **Episodic** | Session history | JSONL transcript search | CLUSTER+ |
| **Semantic** | Concept-level | Hybrid embeddings + KONO spatial | CLUSTER+ |

### Memory Flow

```
Store("key", value, type="stm")
    |
    v
[STM] -- access_count >= 3 --> [Consolidator]
                                    |
                                    v
                              [Coherence Gate]
                                    |
                            pass    |    fail
                            +-------+-------+
                            v               v
                          [LTM]          [Discard]
```

### Power-Level Gating

Memory access is enforced mechanically. Calling `memory.recall(query, memory_type="episodic")` at THREAD level raises `MemoryAccessDenied`. No workarounds, no arguments.

### Consolidation

The `MemoryConsolidator` promotes STM entries to LTM when:

1. **Access count >= 3** (entry was recalled multiple times)
2. **Coherence gate passes** (content is meaningful, not gibberish)

Consolidation runs automatically after each `orchestrator.execute()` call at FORK+ levels.

---

## Team Patterns

### Swarm (CLUSTER)

Independent parallel work. Each teammate owns a specific aspect.

**Default roles**: analyst, implementer, reviewer, explorer, documenter

```
[Lead]
  |--- [analyst]      -> independent analysis
  |--- [implementer]  -> code implementation
  |--- [reviewer]     -> code review
  |--- [explorer]     -> codebase exploration (haiku)
  |--- [documenter]   -> documentation (haiku)
  |
  v
[Merge: concatenate]
```

### Pipeline (PIPELINE)

Sequential stages with dependencies. Output of each stage becomes input for the next.

**Default roles**: researcher, architect (opus), implementer-1, implementer-2, tester, reviewer

```
[researcher] -> [architect] -> [implementer-1] -> [implementer-2] -> [tester] -> [reviewer]
                                       |
                              [quality gates between stages]
```

### Council (MESH)

Independent analysis followed by review, voting, and synthesis. Dissenting opinions are recorded.

**Default roles**:

| Role | Specialty | Model |
|------|-----------|-------|
| pragmatist | Practical engineering trade-offs | Opus |
| theorist | Algorithmic and theoretical analysis | Opus |
| security-advisor | Security and risk assessment | Sonnet |
| ux-advocate | User experience and API design | Sonnet |
| devil-advocate | Challenging assumptions and finding flaws | Opus |
| integrator | Synthesizing viewpoints into decisions | Opus |

```
Phase 1: Independent Analysis
  [pragmatist] [theorist] [security] [ux] [devil]
        \         |          |        |      /
         \        |          |        |     /
          v       v          v        v    v
Phase 2: Integration
              [integrator]
                   |
                   v
          [Final Decision + Dissent Notes]
```

**Merge strategy**: `vote_and_synthesize`

---

## Graceful Degradation

When Agent Teams feature is unavailable (`CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS != "1"`):

- CLUSTER/PIPELINE/MESH fall back to Task tool subagents
- Max agents reduced to power level's Task tool capacity
- Team patterns degrade to parallel Task tool calls
- Memory and reasoning still operate at the requested power level

---

## Orchestrator Pipeline

The `BrolyOrchestrator.execute()` method runs 7 steps:

1. **Memory retrieval** - Search all accessible memory types for relevant context
2. **Escalation check** - Compare task against mode escalation signals
3. **Context check** - Verify context window meets power level requirements
4. **Framework selection** - Auto-select or use hint/override
5. **Prompt generation** - Generate structured reasoning prompt
6. **Execution** - Dispatch to appropriate mechanism (solo/task/team)
7. **Post-execution** - Store in STM, consolidate memories, log execution

### Mode Integration

Modes can configure Broly via YAML:

```yaml
broly:
  enabled: true
  default_level: thread
  reasoning_hint: cot
  escalation:
    to: cluster
    signals:
      - "complex multi-file refactor"
      - "security audit"
```

The orchestrator's `assess_escalation()` method checks task descriptions against these signals and auto-escalates when matched.

---

## Command Reference

| Command | Power Level | Description |
|---------|------------|-------------|
| `/broly thread "task"` | THREAD | Single-session reasoning |
| `/broly fork "task"` | FORK | 2-3 Task tool subagents |
| `/broly cluster "task"` | CLUSTER | 5-7 agent swarm |
| `/broly pipeline "task"` | PIPELINE | 7-10 agent pipeline (requires `/extend-context`) |
| `/broly mesh "task"` | MESH | 10-15 agent council (requires `/extend-context`) |
| `/broly auto "task"` | Auto | Starts at THREAD, escalates based on mode config |

### Prerequisites

- **PIPELINE/MESH**: Opus 1M context (default) is sufficient; no model switch needed
- **CLUSTER+**: Set `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1` for full team support

---

## Configuration

### Max Recursion Depth

`MAX_RECURSION_DEPTH = 3` (Team lead -> teammate -> Task subagent)

### Token Multipliers

| Level | Multiplier |
|-------|-----------|
| THREAD | 1.0x |
| FORK | 2.0x |
| CLUSTER | 5.0x |
| PIPELINE | 5.0x |
| MESH | 5.0x |

---

*Concept: Thomas Frumkin (compressed information language specification)*
*Implementation: GRIP framework*
