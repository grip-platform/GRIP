# Modes and Skills Guide

How GRIP modes provide domain-specific operating contexts and how skills power them.

## What Are Modes?

A mode is a named operating context that loads domain-specific skills and instructions. When you activate `code` mode, the agent gains software development skills. Switch to `strategy` mode, and it gains strategic assessment frameworks instead.

Every mode stacks on top of a **foundation layer** that is always active. The foundation provides safety gates, efficiency rules, and design principles that no mode can override.

```
/mode code          --> loads feature-complete, design-principles, code-agentic
/mode strategy      --> loads strat-ops, leading-organisation
/mode off           --> unloads mode skills, foundation stays active
```

## How Modes Work

```
+-------------------------------------------------+
|              ACTIVE MODE (e.g., code)            |
|                                                  |
|  always skills:   feature-complete, design-...   |
|  optional skills: e2e-test-generation, ui-...    |
|  prompt injection: mode-specific instructions    |
|                                                  |
|              STACKS ON TOP OF                    |
|                                                  |
|  +--------------------------------------------+ |
|  |           FOUNDATION LAYER                  | |
|  |           (always active)                   | |
|  |                                             | |
|  |  Safety gates   Efficiency    Design        | |
|  |  (context-gate, (file read,   (SOLID,       | |
|  |   asking-users)  concise)     GRASP, DRY)   | |
|  +--------------------------------------------+ |
+-------------------------------------------------+
```

When you activate a mode:

1. Foundation layer stays active (~1k tokens)
2. Mode `always` skills load automatically
3. Mode `prompt_injection` adds domain instructions
4. Mode `optional` skills become available on demand
5. State persists to `~/.claude/.active-mode` (survives session restart)

When you switch modes, the previous mode's skills unload cleanly before the new mode loads.

## Commands

| Command | What It Does |
|---------|-------------|
| `/mode list` | Show all 25 available modes |
| `/mode code` | Activate code mode |
| `/mode code testing` | Stack two modes (max 3) |
| `/mode off` | Deactivate current mode |
| `/mode status` | Show active mode and loaded skills |

## All 25 Modes at a Glance

### Development

| Mode | Purpose | Token Budget |
|------|---------|-------------|
| **code** | Software development, feature building, bug fixing | ~6k |
| **architect** | System design, ADRs, architecture decisions | ~3.5k |
| **automation** | Workflow orchestration with safety guardrails | ~4.5k |

### Knowledge Work

| Mode | Purpose | Token Budget |
|------|---------|-------------|
| **research** | Deep research, evidence synthesis (PICO + CRAAP) | ~3k |
| **synthesis** | Cross-domain knowledge integration | ~3.5k |
| **analysis** | Structured analysis with RIPER phases | ~4k |
| **writing** | Long-form writing, editing, voice consistency | ~3k |
| **learning** | Personal learning pathway design | ~3.5k |

### Decision and Strategy

| Mode | Purpose | Token Budget |
|------|---------|-------------|
| **strategy** | Gap analysis, red-teaming, strategic assessment | ~3.5k |
| **decision** | Structured decision frameworks, criteria weighting | ~3k |
| **brainstorm** | Divergent thinking, ideation (criticism suspended) | ~2.5k |
| **business** | Founder operations, team leadership | ~2.5k |

### Review and Quality

| Mode | Purpose | Token Budget |
|------|---------|-------------|
| **review** | Unified code/content/document review | ~3.5k |
| **testing** | Full QA lifecycle with human-in-the-loop gates | ~5.5k |
| **security** | STRIDE, OWASP, DREAD security auditing | ~3.5k |

### Content and Communication

| Mode | Purpose | Token Budget |
|------|---------|-------------|
| **marketing** | Authentic content creation (anti-AI slop) | ~2.5k |
| **legal** | Contract drafting, legal documents | ~2.5k |
| **teaching** | Educational content, curriculum design | ~3k |

### Collaboration

| Mode | Purpose | Token Budget |
|------|---------|-------------|
| **pair** | Remote session monitoring and steering | ~3k |
| **planning** | Daily team planning and task prioritisation | ~3.5k |

### Utility

| Mode | Purpose | Token Budget |
|------|---------|-------------|
| **demo** | Live demonstrations and capability showcase | ~2.5k |
| **create** | Meta-mode for creating new modes | ~2k |
| **upskilling** | Meta-skill creation with recursive learning | ~5.5k |
| **incognito** | Private conversation, no persistence | ~500 |
| **flow** | Trust-based permission bypass (hidden, high risk) | ~500 |

## Mode Stacking

Stack up to 3 modes for cross-domain work. Skills load additively. Foundation stays singular (not duplicated).

```bash
/mode code testing          # Development + QA
/mode code security         # Secure coding
/mode research synthesis    # Deep research + integration
/mode strategy decision     # Assessment + structured choice
/mode writing marketing     # Long-form + authentic voice
```

Good stacking combinations:

| Combination | Use Case |
|-------------|----------|
| code + testing | Feature development with integrated QA |
| code + security | Secure coding with vulnerability awareness |
| research + synthesis | Investigation with cross-domain integration |
| strategy + decision | Assessment leading to locked decisions |
| architect + code | Design decisions flowing into implementation |

## What Are Skills?

Skills are the building blocks that modes load. They are markdown files in `~/.claude/skills/` that define protocols, best practices, and domain knowledge.

**Skills are passive reference** - they define what to do. **Modes are active context** - they decide which skills to load and when.

### Skill Types Within a Mode

| Type | Behaviour | Example |
|------|-----------|---------|
| **always** | Loaded automatically when mode activates | `feature-complete` in code mode |
| **optional** | Available on demand, not auto-loaded | `e2e-test-generation` in code mode |
| **foundation** | Always active regardless of mode | `context-gate`, `asking-users` |

### Example: Code Mode Skills

```
Foundation (always active):
  context-gate, asking-users, forging-skills, design-principles

Code Mode (always):
  feature-complete    - 7-phase feature development workflow
  design-principles   - SOLID, GRASP, DRY, KISS, YAGNI
  code-agentic        - Verification gates, rollback mechanisms

Code Mode (optional, loaded on demand):
  e2e-test-generation - Playwright/Vitest test scaffolding
  ui-complete         - Frontend design + mobile responsive
  gitops              - Trunk-based development workflow
  pr-automation       - PR creation automation
```

## Creating Custom Modes

Use `/mode create` to launch the mode creation wizard:

1. Provide a name (lowercase, hyphens allowed)
2. Write a description (must include "Use when")
3. Define triggers (commands and natural language)
4. Select always/optional skills (must exist in `~/.claude/skills/`)
5. Set token budget estimate
6. Write prompt injection text

Output: YAML file saved to `~/.claude/modes/definitions/{name}-mode.yaml`

### Mode Definition Format

```yaml
name: my-mode
description: "Use when doing X"
version: 1.0.0
triggers:
  - /mode my-mode

skills:
  always:
    - skill-one
    - skill-two
  optional:
    - skill-three

token_budget: ~3000
foundation: true

prompt_injection: |
  [MODE: MY-MODE]
  Domain-specific instructions here.
```

## Priority Resolution

When skills overlap, priority resolves top-down:

| Priority | Layer | Example |
|----------|-------|---------|
| 1 (highest) | Foundation PARAMOUNT | context-gate always fires |
| 2 | Mode always skills | design-principles guides decisions |
| 3 | Mode optional skills | e2e-test-generation when invoked |
| 4 (lowest) | Ad-hoc skill loads | Via `/skill-name` command |

Foundation gates cannot be bypassed by any mode skill.

## Broly Integration

15 of the 25 modes have Broly (the meta-agent) auto-active. Broly scales agent count based on task complexity:

| Level | Agents | Modes |
|-------|--------|-------|
| THREAD | 1 | De-escalation target for all modes |
| FORK | 2-3 | code, automation, legal, business, brainstorm, review |
| CLUSTER | up to 7 | research, synthesis, analysis, testing, security |
| PIPELINE | up to 10 | architect, planning |
| MESH | up to 15 | strategy, pair |

10 modes have Broly disabled for focused single-threaded work: writing, learning, decision, marketing, teaching, demo, create, upskilling, incognito, flow.

## Further Reading

- [Modes Guide](MODES-GUIDE.md) - Detailed description of each mode with workflows
- [Modes Architecture](MODES-ARCHITECTURE.md) - Technical inheritance model and schema
