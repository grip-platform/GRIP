---
description: PARAMOUNT unified entry point for all human-in-the-loop interactivity. Routes AskUserQuestion, wizards, decision matrices, typed form input, and MCP elicitation through one quality-gated dispatcher.
disable-model-invocation: false
---

# /hitl — Human-In-The-Loop Unified Dispatch

The one entry point for every interactive surface in GRIP. Counterpart to `/ultrado` for loops.

## What it does

Routes every operator-facing question through the right primitive (`AskUserQuestion` for main agent, `elicitation/create` for MCP servers) and the right pattern (single question, wizard, decision matrix, typed form input, confirmation).

## Two axes

**Which schema the harness handed you** — Claude Code carries two `AskUserQuestion` schemas and picks at runtime. The older *dialog* has choice questions only and requires an option `description`. The newer *form* adds `kind: "text"` and `kind: "number"`, and marks `description` optional. You detect this; you do not choose it. See `creating-form-input`.

**The shape you are building** — your choice, orthogonal to the schema.

| Shape | Route | Skill |
|---|---|---|
| 1 question, main agent | A | `asking-users` |
| 1 question, MCP mid-execution | B | `mcp-elicitation` |
| 2-6 related questions, sequential | C | `creating-wizards` |
| 3-8 pre-computed decisions | D | `hitl-decision-matrix` |
| Typed or multi-field intake | E | `creating-form-input` |

Routes C, D and E are all batched `AskUserQuestion` calls — not separate tools. Only Route B is a distinct primitive.

## Universal quality gates (every route)

- ≤12-char headers
- 2-4 options per **choice** question; a `text` or `number` question carries none
- `min` and `max` required on a `number` question
- Every question teaches + collects (bidirectional)
- Never mention "Other" in text
- Every question ends with `?`
- Defer always available
- Persist or discard explicitly
- ELI5 preamble for multi-decision flows
- Cascade detection on reframes

## Usage

```text
/hitl                         # dispatches based on current context
/hitl decision-matrix D1-D6   # force Route D with specific decision list
/hitl wizard onboarding       # force Route C
```

## Activation

- User types `/hitl`
- Agent detects interactive need and reaches for routing logic
- Agent hits Rule 7 confidence gate (<99.9999999%) and needs operator input
- Operator wants to audit recent interactive flows for gate compliance

## See also

- `skills/hitl/SKILL.md` — full dispatch contract + quality gates + anti-patterns
- `skills/hitl-decision-matrix/SKILL.md` — Route D specification with 5-phase flow
- `skills/asking-users/SKILL.md` — Route A primitive
- `skills/creating-wizards/SKILL.md` — Route C patterns
- `skills/mcp-elicitation/SKILL.md` — Route B primitive
- `rules/efficiency-rules.md` Rule 7 — the confidence gate that routes through `/hitl`
