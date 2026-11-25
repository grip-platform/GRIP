# /broly

Invoke the Broly recursive meta-agent with configurable power levels.

## Usage

```bash
/broly <power_level> <task>
/broly auto <task>           # Auto-detect power level (default: THREAD)
/broly fork <task>           # Task tool subagents (2-3 agents)
/broly cluster <task>        # Agent Team swarm (5-7 agents)
/broly pipeline <task>       # Agent Team pipeline (7-10 agents)
/broly mesh <task>           # Agent Team council (10-15 agents)
/broly status                # Show current Broly status
```

## Power Levels

| Level | Mechanism | Agents | Best For |
|-------|-----------|--------|----------|
| `auto`/`thread` | Single session | 1 | Simple tasks, quick analysis |
| `fork` | Task subagents | 2-3 | Research + implementation splits |
| `cluster` | Agent Team swarm | 5-7 | Parallel analysis from multiple angles |
| `pipeline` | Agent Team pipeline | 7-10 | Complex multi-stage workflows |
| `mesh` | Agent Team council | 10-15 | Critical architectural decisions |

## Examples

```bash
# Quick analysis (single agent, CoT reasoning)
/broly auto Summarize what we did last session

# Research with parallel exploration (2-3 subagents, ToT reasoning)
/broly fork Research authentication approaches for this app

# Deep parallel analysis (5-7 teammates, swarm pattern)
/broly cluster Analyze codebase security vulnerabilities

# Multi-stage pipeline (7-10 teammates, sequential stages)
/broly pipeline Refactor the payment system with full test coverage

# Council decision (10-15 specialists deliberate)
/broly mesh Design the microservices architecture for v2
```

## Execution Protocol

When invoked, the agent will:

1. Parse the power level and task from arguments
2. Initialize BrolyOrchestrator at the specified power level
3. Retrieve relevant memory context
4. Auto-select reasoning framework (or accept user override)
5. Generate execution plan appropriate to the mechanism
6. For THREAD: execute directly
7. For FORK: spawn Task tool subagents
8. For CLUSTER+: configure and spawn Agent Team
9. Merge results and consolidate memory

## Implementation

```python
import sys
sys.path.insert(0, str(Path.home() / ".claude" / "lib"))
from broly import BrolyOrchestrator

# Parse args
level = args[0] if args else "auto"
task = " ".join(args[1:]) if len(args) > 1 else ""

orch = BrolyOrchestrator(power_level=level)
result = orch.execute(task)
```

## Concept Attribution

Concept: a collaborator (compressed information language specification)
Implementation: GRIP framework
