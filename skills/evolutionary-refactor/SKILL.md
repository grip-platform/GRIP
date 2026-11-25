---
name: evolutionary-refactor
description: Bio-mimicry evolutionary system for GRIP components. Scans skills, agents, modes, and MCP tools for fitness, detects duplicates, validates naming, and proposes prune/rename/enhance/add actions. Archive, never delete.
status: Active
effort: medium
version: 1.0.0
triggers:
  - /evolve
  - "evolutionary refactor"
  - "prune unused"
  - "component audit"
integrates:
  - pruning-unused-skills
  - self-improvement-engine
  - design-principles
---

# Evolutionary Refactoring

**Purpose:** Automated natural selection for GRIP components. Components that provide value survive and get enhanced; unused components decay and are archived.

## Quick Start

```bash
# Scan + report only
python3 lib/evolutionary_refactor.py audit

# Scan + generate proposals + GitHub issue
python3 lib/evolutionary_refactor.py propose

# Scan + propose + archive (with --confirm gate)
python3 lib/evolutionary_refactor.py execute --confirm

# Show fitness scores (worst first)
python3 lib/evolutionary_refactor.py fitness

# Show duplicate components
python3 lib/evolutionary_refactor.py duplicates
```

## Modes

| Mode | What It Does | Safety |
|------|-------------|--------|
| `audit` | Scan + report only | Read-only |
| `propose` | Scan + GitHub issue | Creates issue |
| `execute` | Scan + archive dead components | Requires `--confirm` |

## Fitness Model

KONO-inspired decay formula:

```
fitness = base * 0.95^days_since_use * (1 + 0.1 * min(use_count, 10))
```

| Parameter | Value | Rationale |
|-----------|-------|-----------|
| Base (used) | 0.6 | Components with any usage start higher |
| Base (unused) | 0.3 | Never-used components start low |
| Decay rate | 0.95/day | ~40% loss in 10 days without use |
| Use bonus cap | 10 | Diminishing returns above 10 uses |

## Tiers

| Tier | Fitness | Action |
|------|---------|--------|
| Essential | >= 0.8 | Protected, never prune |
| Active | 0.5 - 0.8 | Healthy, consider enhancement |
| Dormant | 0.2 - 0.5 | Monitor, may need revival or archive |
| Dead | < 0.2 | Archive candidate |

## Protected Components

These are never pruned regardless of fitness:

- **Skills**: context-gate, asking-users, concise-communication, design-principles, mode, forging-skills, ultrathink, code-mode, flow-mode, pair-mode
- **Agents**: broly, context-refresh, dependency-guardian, pair-worker, pr-workflow
- **Modes**: code-mode, pair-mode, flow-mode

Only `asking-users` from the HITL family is on this list; its siblings `creating-wizards` and the `hitl` dispatch contract are not. The names mirror `PROTECTED` in `lib/evolutionary_refactor.py`.

## Naming Rules

| Type | Convention | Example |
|------|-----------|---------|
| Skills | Gerund form | `creating-wizards`, `optimizing-images` |
| Commands | Short, max 2 words | `/evolve`, `/create-pr` |
| Agents | Descriptive role | `sprint-loop-agent` |
| Exempt | Brand names | `broly`, `kono`, `ultrathink` |

## Safety

- **Archive, never delete**: Components go to `.archive/YYYY-MM-DD/` with manifest
- **AskUserQuestion gate**: Execute mode requires explicit `--confirm`
- **Protected list**: Cannot be overridden programmatically
- **Goodhart integration**: Evolutionary refactoring counts as meta-work toward 25% cap

## Integration Points

- **Workflow**: `data/copilot/workflows/evolutionary-cycle.yaml` (weekly Friday)
- **Metrics**: Uses `learning/metrics.jsonl` for usage data
- **RSI**: Proposals feed into RSI candidate pipeline
- **Goodhart Guard**: Complexity ceiling check in `lib/goodhart_guard.py`
