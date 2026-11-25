# A2A + MCP Coexistence Architecture

## Overview

GRIP uses two complementary protocols operating at different abstraction layers:

| Protocol | Level | Purpose | Example |
|----------|-------|---------|---------|
| **MCP** (Model Context Protocol) | Tool | Agent calls tools | Read file, search code, execute command |
| **A2A** (Agent-to-Agent) | Agent | Agent delegates to agent | Invoke Broly, request code review, run RSI |

These protocols are **complementary**, not competing. Each solves a distinct problem in agent architecture.

## Key Distinction

### MCP: Tool Access Layer

MCP servers expose discrete tools that agents call to interact with external systems:
- `grip_file_read` — read a file
- `grip_git_status` — check git state
- `grip_kanban` — display Kanban board
- `grip_slack_postmessage` — send Slack messages

GRIP's MCP servers:
- `grip-pair` — pair-mode session control
- `grip-apps` — MCP app generator and registry
- `grip-slack` — Slack API integration

**Scope**: Single, isolated tool invocations within the same agent session.

### A2A: Agent Delegation Layer

A2A enables one agent to delegate complex, multi-step tasks to another agent:
- External agent (Gemini) delegates code review to GRIP/Claude
- GRIP agent invokes Broly for comprehensive analysis
- Cross-provider task orchestration and agent composition

**Scope**: Entire task lifecycle, autonomous reasoning, multi-step improvement loops.

## Protocol Boundaries

```
+------------------+     A2A Protocol      +------------------+
|   External Agent |  ==================>  |   GRIP Agent     |
|   (Gemini, etc)  |  <==================  |   (Claude Code)  |
+------------------+  Task lifecycle msgs  +------------------+
                                                    |
                                              MCP Protocol
                                                    |
                                           +--------+--------+
                                           |  MCP Servers    |
                                           |  (tools, data)  |
                                           +-----------------+
```

### When to Use MCP

Use MCP for:
- Single, discrete operations (read, write, search)
- Tool calls within the same agent session
- Data retrieval and mutation
- Operating system interaction
- External service integration (Slack, GitHub)

Example: "Read file X and search for pattern Y" = 2 MCP calls.

### When to Use A2A

Use A2A for:
- Delegating multi-step tasks to another agent
- Cross-provider communication (Gemini -> Claude, Claude -> specialized agent)
- Task lifecycle management (create, stream, complete, cancel)
- When delegated work requires autonomous reasoning or iterative improvement
- Agent composition and orchestration

Example: "Review this PR and iterate until review score > 85" = 1 A2A task (internally calls MCP as needed).

## Architecture

### Discovery

**Agent Card** (`.well-known/agent.json`):
```json
{
  "name": "GRIP",
  "version": "6.0.0",
  "description": "Agent automation and reasoning platform",
  "capabilities": ["converge", "pair-mode", "broly"],
  "endpoints": {
    "a2a_task": "https://grip.internal/a2a/task",
    "agent_card": "https://grip.internal/.well-known/agent.json"
  },
  "authentication": "bearer_token"
}
```

**MCP Server Manifests**: Standard MCP server startup configuration specifying available tools.

### Task Flow (A2A Example)

```
1. External agent discovers GRIP via Agent Card
2. External agent creates A2A task
   POST /a2a/task
   {
     "action": "converge",
     "prompt": "Review the PR for security issues",
     "criterion": "review_score >= 85",
     "max_depth": 3
   }

3. GRIP's A2A adapter receives task:
   - Maps to converge() execution
   - Initializes session context
   - Starts autonomous loop

4. During execution, improve() may call MCP tools:
   - grip_file_read(filename)
   - grip_git_diff(branch)
   - grip_kanban(status)

5. Task events stream back via A2A:
   {
     "event": "depth_complete",
     "depth": 1,
     "score": 73,
     "continue": true
   }

6. Final result returned when criterion met:
   {
     "event": "task_complete",
     "criterion_met": true,
     "depth": 2,
     "final_score": 87,
     "artifacts": [...]
   }
```

### Adapter Layer (`lib/a2a_adapter.py`)

The adapter bridges MCP and A2A worlds:

```python
class A2AAdapter:
    def task_create(self, task_spec):
        """Map A2A task to converge() init"""
        return converge(
            task=task_spec['action'],
            prompt=task_spec['prompt'],
            criterion=task_spec['criterion'],
            max_depth=task_spec.get('max_depth', 2)
        )

    def task_execute(self, task_id):
        """Run improvement loop at each depth"""
        # Each depth may call MCP tools
        yield improve(task_id)

    def task_stream(self, task_id):
        """Stream task progress events via A2A"""
        for event in task_events(task_id):
            yield event

    def task_complete(self, task_id):
        """Check criterion and finalize"""
        return criterion_met(task_id)

    def task_cancel(self, task_id):
        """Circuit breaker for hung tasks"""
        return cancel_and_log(task_id)
```

## Non-Conflicts

MCP and A2A do **NOT** conflict because:

### 1. Different Communication Channels
- **MCP**: stdio, SSE, or WebSocket (agent ↔ tool server)
- **A2A**: HTTP REST, with optional WebSocket for streaming (agent ↔ agent)

### 2. Different Scopes
- **MCP**: Within-agent tool calls (single operation)
- **A2A**: Between-agent task delegation (multi-step lifecycle)

### 3. Composability
A2A task execution can call MCP tools internally:
```
External Agent --A2A--> GRIP Agent ---MCP---> MCP Servers
                            |
                        improve() loop
                            |
                        calls MCP tools
```

### 4. Independent Lifecycle
- MCP servers start/stop independently of A2A tasks
- Multiple A2A tasks can run concurrently, each calling MCP tools
- Task cancellation does not affect MCP servers
- MCP server failure does not cascade to A2A protocol

## Example: Broly Code Review via A2A

```python
# External agent (Gemini) delegates via A2A
task = a2a_create(
    agent="grip",
    action="converge",
    prompt="Run Broly on this PR: {pr_url}",
    criterion="broly_score >= 85 AND all_dimensions_pass",
    max_depth=3
)

# GRIP agent receives task
# Internally, Broly improves may call:
# - grip_file_read (MCP) for source files
# - grip_git_diff (MCP) for PR diff
# - grip_kanban (MCP) for backlog context
# - grip_slack_postmessage (MCP) to post results

# Stream events back to Gemini
for event in a2a_stream(task):
    print(f"Depth {event['depth']}: {event['score']}")

# Final result
result = a2a_wait(task)
```

## Current Implementation Status

| Component | Status | Notes |
|-----------|--------|-------|
| MCP servers (grip-pair, grip-apps) | Production | Well-tested, 3+ agent integrations |
| MCP Slack integration | Production | Handles messaging, file upload, reactions |
| A2A Agent Card | PR #731 | Specifies capabilities and endpoints |
| A2A Task Adapter | PR #342 | Core bridging logic (this PR) |
| A2A HTTP endpoint | Pending | REST API server for task lifecycle |
| A2A Gemini Bridge | Experimental | Antigravity CLI (agy) / Gemini-specific integration |
| Multi-provider bridge | Planned | Generalise bridge for any provider |

## Future Work

### Phase 1: Foundation (Current)
- A2A HTTP endpoint (REST API)
- Agent Card discovery
- Task lifecycle (create, stream, complete, cancel)
- Adapter integration with converge()

### Phase 2: Reliability & Observability
- Task monitoring dashboard
- Event persistence and replay
- Metrics: task latency, criterion-met rate, depth distribution
- Circuit breaker and timeout handling

### Phase 3: Multi-Agent Orchestration
- Bidirectional delegation: GRIP -> external agents
- Multi-hop task chains (A -> B -> C)
- Distributed state tracking across agents
- Task result caching and deduplication

### Phase 4: Cross-Provider Generalization
- MCP as substrate for A2A
- A2A bridges for OpenAI, Anthropic, open-source models
- Unified agent marketplace

## Design Principles

1. **Separation of Concerns**: MCP handles tools, A2A handles agents
2. **Composability**: A2A can use MCP internally; no restrictions
3. **Independence**: Protocol failures isolated to their scope
4. **Discoverability**: Agent Card enables cross-provider discovery
5. **Streaming**: Real-time task progress without polling
6. **Cancelability**: Any task can be cancelled mid-execution

## References

- [MCP Specification](https://modelcontextprotocol.io/)
- GRIP `/converge` protocol: `docs/CONVERGE-PROTOCOL.md`
- GRIP A2A Adapter: `lib/a2a_adapter.py`
- Agent Card standard: `docs/AGENT-CARD-SPEC.md`

---

*A2A + MCP coexistence enables flexible, composable agent architectures without protocol conflicts.*
