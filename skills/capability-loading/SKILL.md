---
name: capability-loading
description: Defines which skills load for a given prompt, given prompt-routing's pattern recommendation. Policy that fires inside prompt-routing's assessment flow — not a standalone slash command.
status: Active
version: 1.0.0
created: 2026-04-27
chains_from: prompt-routing
see_also:
  - prompt-routing
  - execution-router
  - recall
---

# Capability Loading

Given a user prompt and `prompt-routing`'s recommended execution pattern, this
policy determines which skills are loaded into the active context.

## Output Schema

```json
{
  "loaded_skills": ["skill-a", "skill-b"],
  "confidence": 0.85,
  "fallback": "recall"
}
```

`confidence` is 0–1. Below 0.6, always include `recall` as a loaded skill
regardless of pattern. `fallback` names the single skill that fires if pattern matching
produces no loaded skills.

## Pattern-to-Skill Map

| Pattern (from prompt-routing) | Always-loaded skills | Conditionally loaded |
|-------------------------------|----------------------|----------------------|
| Direct | concise-communication, recall | — |
| Focused | recall, design-principles | code-agentic (if code task) |
| Sprint | recall, bg-dispatch, design-principles | hypothesis-driven-development |
| FullMesh | recall, bg-dispatch, ultrathink, autonomous-orchestration | broly-meta-agent, rsi-optimal |

Conditional loading fires when the prompt contains evidence of the condition (e.g.
code tokens for `code-agentic`, falsifiable claim for `hypothesis-driven-development`).

## How This Fires

`capability-loading` is not a slash command. It is policy evaluated inside
`prompt-routing`'s assessment flow after `recommended_pattern` is determined.
The result (`loaded_skills`) is passed to `lib/prompt_routing.py` which records
it in `state/routing/last-assessment.json`.

The agent reads that file at the start of each turn (see `prompt-routing` execution
protocol) and uses the skill list to inform which playbooks are active for that turn.

## Fallback Invariant

If `lib/prompt_routing.py` raises any exception during capability loading, the
fallback is always `recall` only. Capability loading never blocks a prompt.

## Falsification

This skill is wrong if:
- Loaded skill lists are ignored by the agent in practice (no observable effect on tool choice)
- Conditional loading fires on false positives > 20% of sessions (measured via `/evo` friction analytics)
- The pattern-to-skill map drifts from actual skill inventory (verify via `skills/INDEX.md`)

Primary Author: V>>
