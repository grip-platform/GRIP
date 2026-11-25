# AGORA — Multi-LLM Peer Reasoning Protocol

> *Greek: ἀγορά — public square, the place of equal voice and open deliberation*

A horizontal protocol for multi-LLM reasoning. Each agent has equal say,
self-terminates, and can defer to outsiders for a fresh perspective.

## What's in the package

| Module | Purpose |
|--------|---------|
| `message.py` | Wire format: `Decision`, `Message`, `Response` |
| `agent.py` | `AgoraAgent` — one LLM with state and decision parsing |
| `bus.py` | `MessageBus` — in-process router (V2 swaps to Redis/NATS) |
| `runner.py` | `ConversationRunner` — orchestrates rounds, enforces caps |
| `supervisors.py` | Scoped meta-watchers; V1 ships Falsifier gate |
| `prompts.py` | Default system prompts (persona-default-off) |

## Quick start

```python
from lib.agora import (
    AgoraAgent, ConversationRunner, MessageBus, Message, RunnerLimits,
)
from lib.hal.tool_runtime import make_anthropic_chat_fn

bus = MessageBus()
chat_fn = make_anthropic_chat_fn(api_key="...", model="claude-opus-4-7", max_tokens=4096)

agents = [
    AgoraAgent(agent_id="claude_1", chat_fn=chat_fn),
    AgoraAgent(agent_id="claude_2", chat_fn=chat_fn),
]
runner = ConversationRunner(bus, RunnerLimits(turn_cap=10))
opening = Message(sender_id="user", recipients=["*"], content="What's 2+2?")
result = runner.run(agents, opening)
print(f"Terminated: {result.terminated_reason} after {result.turns} turns")
```

## Design references

- Spec: `drafts/agora-multi-llm-protocol-2026-05-02.md`
- Build log: `drafts/agora-build-log-2026-05-02.md`
- Hypothesis: H269 (prototype <500 LOC + 80% convergence rate, deadline 2026-06-15)
- Hypothesis: H270 (absorption-no-regression vs /broly MESH, deadline 2026-08-15)

## V1 scope (this commit)

- ✓ Core types (`message.py`)
- ✓ Agent class with JSON decision parsing (`agent.py`)
- ✓ In-process bus with quorum-based termination (`bus.py`)
- ✓ Round-robin runner with skip-self + system-sender stop-signal (`runner.py`)
- ✓ Falsifier supervisor — Stage 1 mechanical gate (`supervisors.py`)
- ✓ 30 tests covering all modules

## Deferred to W2+

- LLM agent escalation (Stage 2 of Falsifier)
- Four other supervisors (Hallucination, Sycophancy, Domain Drift, Agent Dominance)
- Out-of-process bus (Redis/NATS shim)
- Defer routing (defer is parsed but not yet routed to outsiders)
- Slash command `/agora`
- /broly absorption (W3.5)
