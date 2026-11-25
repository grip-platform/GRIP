# Parallel-Default over Either-Or (PARAMOUNT)

Origin: 2026-06-27, V>>: *"why do you always say this **or** that while you can do
things in parallel with Agents, and/or actual different LLMs? You can do
everything."* A recurring correction — the assistant kept surfacing either/or
MENUS ("say idr or opencode", "opencode or /create") for workstreams that were
NOT mutually exclusive, serialising work the operator wanted done concurrently.
The artifact `/create` emitted for this achievement (the gate refused a premature
council-skill at N=2; this rule is the load-bearing crystallisation instead).

## The law

> When a turn contains **≥2 independent, non-mutually-exclusive actionable items,
> DO them — do not surface an either/or choice.** "Which one?" is reserved for
> GENUINELY mutually-exclusive forks (the irreversible / direction-A-vs-B HALT
> class in `operational-context.md` "No self-imposed blockers"). Everything else:
> execute in parallel.

## Pick the cheapest fitting parallel mechanism (in order)

1. **Batched tool calls in one message** — FREE parallelism. Independent
   reads / probes / edits / Bash go in ONE message, never N sequential turns.
   Always try this first.
2. **Agents (`Task`, background)** — when each workstream's useful work clears the
   `dispatch-economics.md` Gate-2 bar (≥ ~3× the agent floor) AND they are
   independent. Fan out; integrate on completion.
3. **Different LLMs via HAL** — when the work is cognition that can run
   off-Anthropic (the `grip-sovereign-council` pattern: N speakers + an exogenous
   judge, in parallel). Use the substrate, not just the REPL.

## What this is NOT

NOT "always spawn agents" — that violates `dispatch-economics.md` (over-fan-out
floor-waste). The failure corrected is **timid serialisation into either/or
menus**, not under-dispatching. The cheapest mechanism that fits wins; often that
is batched main-thread tool calls, not agents.

## Relationship

Sharpens **Execution-Bias** (project `CLAUDE.md`: act, don't over-plan) with the
PARALLELISM dimension it lacked. Bounded by `dispatch-economics.md` (WHEN agents
are economical), `agent-dispatch-protocol.md` (HOW to dispatch safely), and the
HALT classes (the genuine forks that DO warrant a choice). Sibling to
`loop-priority-external-over-theatre.md` (spend parallel effort on external value).

## Falsification

Wrong if, over ≥10 turns it governs: parallelising non-exclusive work produces
more rework/collisions than serial would have (→ serialise that class); OR the
either/or being collapsed was a genuine mutually-exclusive fork (→ asking WAS
correct; narrow the rule, don't drop it); OR batched/agent parallelism measurably
degrades quality vs sequential. Track: operator "why didn't you just do both / why
are you asking" corrections (target 0; was recurring 2026-06-27) and
parallel-induced rework incidents.

## Scope

Every turn with ≥2 independent actionable items — code, research, ops, delivery.
The genuine-fork exception (ask) is exactly the four HALT classes in
`operational-context.md`: irreversible destructive ops, direction-A-vs-B with
materially different outcomes, a real information gap only the operator can fill,
and cross-client commercial decisions.
