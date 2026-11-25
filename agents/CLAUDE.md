# Agents Directory

45 specialized agents for autonomous task execution.

## PARAMOUNT: Fractal Gate Compliance

"Fractal" here means the same gates at both depths Claude Code allows: the main session (depth 0) and its subagents (depth 1, point 4 below). That is two levels, not a deeper hierarchy. Elsewhere the word names other depths: three levels for Broly (`skills/broly-meta-agent/SKILL.md`) and four named levels for full-converge (`skills/full-converge/SKILL.md`).

ALL agent definitions MUST include:
1. **Skills frontmatter**: `dependency-guardian`, `recall`, `design-principles`, `concise-communication`
2. **Gate compliance section** in body: efficiency gates, quality gates, safety gates
3. Hooks fire at depth 1 (verified empirically). `additionalContext` injects gate rules on every subagent tool call.
4. Subagents cannot spawn sub-subagents (CC limitation). Depth is 0 and 1 only.

## PARAMOUNT: JIT Triple-Gate (Gate 0 — All Agents)

ALL agents MUST apply Gate 0 (JIT) during code generation:
- Write correct code on FIRST PASS — apply DRY/KISS/BIG-O/YAGNI/CC/ISP/DIP/Feature Envy inline
- Dispatch tables over if/else chains by default
- Extract helpers on first duplication (not third)
- O(n log n) algorithms by default
- Functions < 30 lines as written (decompose during generation)
- Dependencies injected, not hardcoded

Gate 1 (pre-commit verification) catches what Gate 0 missed. If Gate 1 finds violations, Gate 0 failed — feed to CUBE.
Gate 2 (post-commit) is the audit trail for /evo genome fitness.

This applies to main agent, ALL subagents, ALL modes. No exceptions.
Origin: Gen 470 (CV=0.015), Gen 475 V>> JIT directive.

## Structure

Each agent is defined in a markdown file whose frontmatter gives its `name:` and `description:`. Claude Code also loads the files in subfolders, and GRIP counts them (`lib/asset_counter.py`, `AGENT_GLOB`). This index and `_base-protocol.md` are not agents.
```
agents/
├── agent-name.md
└── bridge/
    └── a2a_bridge.md
```

## Available Agents

| Agent | Purpose | Trigger | Priority |
|-------|---------|---------|----------|
| **Safety/Enforcement (7)** | | | |
| `dry-kiss-enforcer` | DRY + KISS enforcement | Code edits | high |
| `solid-enforcer` | SOLID principles | Code edits | high |
| `grasp-enforcer` | GRASP responsibility | Code edits | high |
| `yagni-enforcer` | Prevent over-engineering | Code edits | high |
| `gentner-enforcer` | Structural mapping gate | Analogies | high |
| `dependency-guardian` | Block wasteful reads | File operations | high |
| `skill-graph-validator` | Circular dep detection | Skill creation | low |
| **Efficiency (3)** | | | |
| `file-read-optimizer` | Cache file reads | File operations | high |
| `efficiency-auditor` | Workflow analysis | Periodic | medium |
| `direct-implementation` | Eliminate temp scripts | Implementation | medium |
| **PR/Code (3)** | | | |
| `pr-workflow` | PR creation | `/create-pr` | medium |
| `pr-analyzer` | PR review | PR opened | medium |
| `markdown-expert` | Markdown linting | Invoked, or `/markdown-lint` | medium |
| **Meta/RSI (3)** | | | |
| `broly` | Multi-framework meta-agent | Complex tasks | high |
| `rsi-orchestrator` | RSI loop orchestration | `/rsi` | high |
| `sprint-loop-agent` | Autonomous sprint exec | Sprint issues | medium |
| **Session (3)** | | | |
| `context-refresh` | Mental model building | Session start | high |
| `history-mining` | Search past sessions | `/remind-yourself` | medium |
| `pair-worker` | Pair-mode task execution | `/pair` | medium |
| **Specialised (3)** | | | |
| `auth-debugging` | OAuth/OIDC debugging | Auth issues | low |
| `project-onboarder` | New project setup | First session | low |
| `epic-summariser` | Multi-issue sprint rollup | Sprint end | low |
| **Maintenance (6, Gen 487)** | | | |
| `worktree-reaper` | Clean stale worktrees | Post-MESH | low |
| `genome-monitor` | Flag stalling genes | Post-/evo | low |
| `kono-reaper` | Prune low-fitness memories | Post-/save | low |
| `learning-janitor` | Deduplicate insights | Post-/learn | low |
| `checkpoint-archiver` | Clean stale checkpoints | Session start | low |
| `lesson-carousel` | Surface cross-session lessons | /recall | low |

## Invoking Agents

Agents are invoked via the Task tool:

```
Task(subagent_type="Explore", prompt="Find error handling code")
Task(subagent_type="Plan", prompt="Plan the authentication feature")
```

## Agent vs Skill

| Aspect | Agent | Skill |
|--------|-------|-------|
| Execution | Active (runs autonomously) | Passive (reference) |
| Context | Isolated | Shared |
| Tools | Has tool access | No tool access |
| Output | Returns result | Defines protocol |

Agents implement protocols defined in skills.
