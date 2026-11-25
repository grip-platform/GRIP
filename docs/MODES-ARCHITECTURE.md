# GRIP Modes Architecture

Version: 1.1.0 | Gen: 274

> For a user-facing guide with detailed descriptions of each mode, see **[Modes Guide](MODES-GUIDE.md)**.

## Overview

GRIP modes provide domain-specific operating contexts that stack skills on top of a persistent foundation layer. This document explains the inheritance model and how skills interact.

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────────┐
│                         ACTIVE MODE                                  │
│                    (e.g., code, strategy)                           │
│                                                                      │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │                    Mode-Specific Skills                      │   │
│  │                                                              │   │
│  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐       │   │
│  │  │   always:    │  │   optional:  │  │   prompt:    │       │   │
│  │  │  - skill-1   │  │  - skill-a   │  │  injection   │       │   │
│  │  │  - skill-2   │  │  - skill-b   │  │  text        │       │   │
│  │  └──────────────┘  └──────────────┘  └──────────────┘       │   │
│  │                                                              │   │
│  │  Token Budget: ~2000-6000 (varies by mode)                   │   │
│  └─────────────────────────────────────────────────────────────┘   │
│                              │                                       │
│                              │ STACKS ON TOP OF                      │
│                              ▼                                       │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │                    FOUNDATION LAYER                          │   │
│  │                    (ALWAYS ACTIVE)                           │   │
│  │                                                              │   │
│  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐       │   │
│  │  │  PARAMOUNT   │  │  Efficiency  │  │   Design     │       │   │
│  │  │   Skills     │  │    Rules     │  │  Principles  │       │   │
│  │  │              │  │              │  │              │       │   │
│  │  │ - ultrathink │  │ - File Read  │  │ - SOLID      │       │   │
│  │  │ - asking-    │  │   Optimize   │  │ - GRASP      │       │   │
│  │  │   users      │  │ - Plan Eval  │  │ - DRY        │       │   │
│  │  │ - context-   │  │   Gate       │  │ - KISS       │       │   │
│  │  │   gate       │  │ - Concise    │  │ - YAGNI      │       │   │
│  │  │ - forging-   │  │   Comms      │  │ - YSH        │       │   │
│  │  │   skills     │  │ - Dependency │  │              │       │   │
│  │  │ - RSI Loop   │  │   Guardian   │  │              │       │   │
│  │  └──────────────┘  └──────────────┘  └──────────────┘       │   │
│  │                                                              │   │
│  │  Token Budget: ~1000 tokens                                  │   │
│  └─────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────┘
```

## Inheritance Model

### Foundation Skills (NEVER Override)

These skills are PARAMOUNT and remain active regardless of mode:

| Skill | Purpose | Cannot Be Overridden By |
|-------|---------|------------------------|
| `ultrathink` | 6 verification gates | Any mode skill |
| `asking-users` | AskUserQuestion MANDATORY | Any mode skill |
| `context-gate` | 80% threshold blocking | Any mode skill |
| `forging-skills` | Skill retention gates | Any mode skill |
| `improvement-tracking` | RSI feedback loop | Any mode skill |
| `design-smell-response` | Heatmap response | Any mode skill |
| `self-improvement-engine` | 10-step RSI cycle | Any mode skill |
| `recursive-learning` | Effectiveness tracking | Any mode skill |
| `autonomous-learning` | Pattern detection | Any mode skill |

### Mode Skills (Stack/Supplement)

Mode-specific skills ADD to foundation, they do not replace it:

```yaml
# Example: code-mode.yaml
skills:
  always:          # Loaded with mode
    - feature-complete
    - design-principles
    - code-agentic
  optional:        # Available on demand
    - e2e-test-generation
    - ui-complete
```

### Stacking Behavior

```
Mode: code
─────────────────────────
Active Skills:
  [Foundation - PARAMOUNT]
  ├── ultrathink
  ├── asking-users
  ├── context-gate
  ├── forging-skills
  └── RSI Loop (5 skills)

  [Code Mode - always]
  ├── feature-complete
  ├── design-principles
  └── code-agentic

  [Code Mode - optional]
  ├── e2e-test-generation (on demand)
  ├── ui-complete (on demand)
  ├── gitops (on demand)
  └── pr-automation (on demand)
```

## Skill Priority Resolution

When skills have overlapping concerns:

| Priority | Layer | Example |
|----------|-------|---------|
| 1 (Highest) | Foundation PARAMOUNT | ultrathink gates always fire |
| 2 | Mode always skills | design-principles guides architecture |
| 3 | Mode optional skills | e2e-test-generation when invoked |
| 4 (Lowest) | Ad-hoc skill loads | Via `/skill-name` command |

### Conflict Resolution Rules

1. **Foundation gates ALWAYS fire** - Mode skills cannot bypass ultrathink or context-gate
2. **Mode skills supplement, not override** - Adding features, not replacing behavior
3. **Optional skills are additive** - Load only when explicitly needed
4. **Prompt injection is mode-specific** - Each mode has unique context text

## Mode Definition Schema

```yaml
name: mode-name
description: Brief description
version: 1.0.0
triggers:
  - /mode name
  - "natural language trigger"

skills:
  always:            # Loaded when mode activates
    - skill-1
    - skill-2
  optional:          # Available but not auto-loaded
    - skill-a
    - skill-b

token_budget: ~3500
foundation: true     # Always true - foundation cannot be disabled
stacks_with:         # Documents what foundation provides
  - ultrathink
  - asking-users
  - context-gate

prompt_injection: |
  [MODE: NAME]
  Mode-specific instructions here.

context:
  - When this mode applies
  - Example use cases
```

## Available Modes

| Mode | Token Budget | Always Skills | Broly | Use Case |
|------|-------------|---------------|-------|----------|
| code | ~6000 | feature-complete, design-principles, code-agentic | FORK | Software development |
| architect | ~3500 | design-principles, adr-generation | PIPELINE | System design and ADRs |
| automation | ~4500 | self-improvement-engine, recursive-learning, safety-guardrails | FORK | Autonomous workflows |
| research | ~3000 | reasoning-with-ai, research-synthesis | CLUSTER | Deep research and evidence synthesis |
| synthesis | ~3500 | research-synthesis, reasoning-with-ai | CLUSTER | Cross-domain knowledge integration |
| analysis | ~4000 | riper-workflow, reasoning-with-ai | CLUSTER | RIPER structured analysis |
| writing | ~3000 | authentic-writing, reasoning-with-ai | disabled | Long-form writing and editing |
| learning | ~3500 | pedagogical-patterns, curriculum-design | disabled | Personal learning pathways |
| strategy | ~3500 | strat-ops, leading-organisation | MESH | Strategic assessment |
| decision | ~3000 | decision-locking, reasoning-with-ai | disabled | Structured decision frameworks |
| brainstorm | ~2500 | reasoning-with-ai | FORK | Ideation and divergent thinking |
| business | ~2500 | leading-organisation | FORK | Founder operations |
| review | ~3500 | reviewing-contributions, design-principles | FORK | Unified code/content/doc review |
| testing | ~5500 | testing-qa-lifecycle, testing-modes-skills, design-principles | CLUSTER | Full QA lifecycle with HITL gates |
| security | ~3500 | vulnerability-detection, threat-modeling, sanitizing-public-releases, deployment-hardening | CLUSTER | Security auditing |
| marketing | ~2500 | authentic-writing, medium-article-writer | disabled | Authentic content creation |
| legal | ~2500 | legal-ops | FORK | Contract drafting |
| teaching | ~3000 | pedagogical-patterns, reasoning-with-ai | disabled | Educational content |
| pair | ~3000 | pair-mode | MESH | Remote session monitoring |
| planning | ~3500 | daily-planning, leading-organisation | PIPELINE | Daily planning and task prioritisation |
| demo | ~2500 | concise-communication, asking-users | disabled | Live demonstrations |
| create | ~2000 | skill-creation-best-practices, creating-skills | disabled | Mode creation wizard |
| upskilling | ~5500 | skill-creation-best-practices, creating-skills, recursive-learning, autonomous-learning, self-improvement-engine, meta-improvement-switch | disabled | Meta-skill creation |
| incognito | ~500 | (none) | disabled | Private conversation |
| flow | ~500 | (none) | disabled | Permission bypass (hidden) |

## Lifecycle

### Activation

```grip
mode.activate⟿ {
    1. foundation.load (PARAMOUNT skills)
    2. mode.definition.parse
    3. mode.skills.always.load
    4. mode.prompt_injection.apply
    5. state.persist (@~/.claude/.active-mode)
    6. emit("[MODE: {name}]")
}
```

### Deactivation

```grip
mode.deactivate⟿ {
    1. mode.skills.unload
    2. prompt_injection.clear
    3. state.clear
    4. emit("[MODE: OFF]")
    5. foundation.persist (PARAMOUNT stays active)
}
```

### Session Restore

```grip
session.start⟿ if(@~/.claude/.active-mode.exists) ⫶ mode.restore
```

## Commands

| Command | Action |
|---------|--------|
| `/mode list` | Show all available modes |
| `/mode {name}` | Activate a mode |
| `/mode off` | Deactivate current mode |
| `/mode status` | Show current mode and loaded skills |

## Token Budget Calculation

Total context cost = Foundation (~1000) + Mode always skills + Optional skills (as loaded)

Example for code mode:
- Foundation: ~1000 tokens
- Code always (3 skills): ~5000 tokens
- Total: ~6000 tokens

## Best Practices

1. **Choose appropriate mode for task** - Don't use code mode for strategic planning
2. **Load optional skills as needed** - Don't preload all optional skills
3. **Foundation is non-negotiable** - ultrathink and context-gate always protect you
4. **Mode switching is clean** - Previous mode skills unload completely
5. **Session restore is automatic** - Active mode persists across sessions

## Related Files

| File | Purpose |
|------|---------|
| `modes/foundation.md` | Foundation layer documentation |
| `modes/MODE.grip` | Mode index and protocol |
| `modes/definitions/*.yaml` | Individual mode definitions |
| `lib/mode-loader.sh` | Mode activation/deactivation logic |

---

**
