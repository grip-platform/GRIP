---
name: test-driven-agents
description: Pattern documentation for parallel TDD agents. Aspirational reference for future implementation.
status: Draft
effort: low
version: 0.1.0
triggers:
  - "test driven agents"
  - "TDD agents"
---

# Test-Driven Parallel Agents

**Purpose:** Document the pattern for spawning parallel feature agents that each follow red-green-refactor. Not yet implemented as active tooling.

**Status:** Draft / Aspirational

## Pattern

### Architecture

```
Coordinator Agent
  |
  +-- Feature Agent 1 (red-green-refactor)
  +-- Feature Agent 2 (red-green-refactor)
  +-- Feature Agent 3 (red-green-refactor)
  |
  +-- Integration Agent (full suite)
```

### Agent Lifecycle

1. **Coordinator** breaks feature into independent sub-tasks
2. Each **Feature Agent** follows TDD:
   - Write failing test (red)
   - Implement minimum code to pass (green)
   - Refactor for quality (refactor)
   - Iterate up to 5 cycles
3. Each agent commits to its own branch
4. **Integration Agent** merges all branches, runs full test suite
5. PR created only if all tests pass

### Constraints

- Each agent operates on isolated file sets (no conflicts)
- Coordinator defines interfaces upfront
- Feature agents cannot modify shared files without coordinator approval
- Maximum 5 red-green-refactor iterations per agent
- Timeout: 10 minutes per agent

### When to Use

- Large feature with clearly separable components
- Each component has well-defined interfaces
- Test infrastructure already exists
- Team has capacity for parallel agent cost

### When NOT to Use

- Tightly coupled changes across shared files
- Exploratory work without clear interfaces
- Simple single-file changes
- No existing test infrastructure

## Future Implementation

When building this:
1. Use TeamCreate for agent coordination
2. Each agent gets pair-worker subagent_type
3. Coordinator uses TaskCreate/TaskUpdate for orchestration
4. Integration via git merge (not file concatenation)
