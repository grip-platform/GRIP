---
name: hitl
description: Dispatch contract for human-in-the-loop interactivity. Documents WHICH primitive to reach for given a task shape (single question, wizard, decision matrix, MCP elicitation, typed form input) and which universal quality gates apply across all of them. /hitl is documentation, not a tool call — primitives (/asking-users, /creating-wizards, /mcp-elicitation, /hitl-decision-matrix, /creating-form-input) remain first-class PARAMOUNT entry points and may be invoked directly. Analogous to /ultrado for loops — a routing layer atop sharp single-purpose primitives.
allowed-tools: AskUserQuestion, Read, Edit, Write, Bash
---

# HITL — Human-In-The-Loop Dispatch Contract

The routing layer over GRIP's interactive primitives. When the agent is about to ask the user anything — a single clarifying question, a design decision, a multi-step wizard, a batched decision matrix, or an MCP-mid-execution prompt — this skill documents which primitive to reach for and which quality gates apply.

## DUAL-PARAMOUNT model (council-ratified 2026-05-21)

`/hitl` is **dispatch-contract documentation**, NOT a mechanical gate. The actual interactive primitives — `/asking-users`, `/creating-wizards`, `/mcp-elicitation`, `/hitl-decision-matrix` — **retain PARAMOUNT primitive status** and **may be invoked directly**. Both entry paths are legitimate and first-class:

- **Direct primitive invocation** — agent (or operator-facing skill) reaches for `/asking-users` or `/creating-wizards` because the task shape is obvious and the quality gates are internalised. Fastest path.
- **Via `/hitl` dispatch** — agent (or skill author) consults this contract to (a) pick the right primitive for a non-obvious shape, (b) audit a flow against the universal quality gates, (c) onboard a new contributor to the four-route taxonomy. Slower, more pedagogical.

The primitives are the load-bearing PARAMOUNT skills. `/hitl` is the cross-cutting routing+gate layer that makes the four primitives behave like a coherent family rather than four ad-hoc surfaces.

## Why this exists

Operator attention is the most expensive resource GRIP consumes. Every interaction that gets the shape wrong (wrong granularity, wrong option count, wrong tool, wrong time) burns operator trust and pushes the system toward the *silently-guess* failure mode (Rule 7 violation). The dispatch contract makes four things uniform across primitives:

1. **Shape → primitive routing is mechanical** — look at the task shape, pick the route. The router lives in this document; the execution lives in the primitive.
2. **Quality gates apply uniformly** — ≤12-char headers, 2-4 options on a choice question, no "Other" mention, bidirectional (teach + collect) — the same gates regardless of which primitive executes the call.
3. **Cross-surface consistency** — main-agent `AskUserQuestion` and MCP-server `elicitation/create` are different tool calls but the same pattern; this contract treats them as interchangeable primitives.
4. **Confidence gating is route-aware** — when confidence <99.9999999%, the correct action is "halt and pick a primitive via this contract", not "proceed with a guess".

Counterpart to `/ultrado` for loops: `/ultrado` is the dispatch surface over autonomous self-improvement loops; `/hitl` is the dispatch surface over interactive primitives. Both are dispatchers, not implementations.

## Dispatch contract — two axes

Dispatch used to read as one flat list of four routes. It is really **two independent choices**, and separating them removes a persistent confusion about whether a wizard is a different tool. It is not.

### Axis 1 — which schema the harness handed you

Claude Code carries **two** `AskUserQuestion` schemas and picks between them at runtime (`get inputSchema(){return oIe()?Tos():wos()}`). You do not choose this. You detect it, and a model is handed exactly one branch with nothing hinting the other exists.

| | Dialog (older) | Form (newer) |
|---|---|---|
| Interaction | Answering resolves it | Fill in, then Submit |
| Question kinds | choice only | `choice`, `text`, `number` |
| Option `description` | Required | Optional, discouraged |
| Skipping | Not offered | Leave any question unanswered |
| Free text | Automatic "Other" option | Type into any question |

`multiSelect` is a **boolean field on a question**, not a third schema.

Full contract, and the house rule that keeps options explained after the form stops requiring it: `skills/creating-form-input/SKILL.md`.

### Axis 2 — the shape you are building

Your choice, and orthogonal to axis 1. The router is mechanical — look at the shape, pick the route.

| Shape | Route | Primitive | Example |
|---|---|---|---|
| 1 question, main agent | A · `asking-users` | `AskUserQuestion` | "Which library — requests or httpx?" |
| 1 question, inside MCP server handler | B · `mcp-elicitation` | `elicitation/create` | An MCP tool needs clarification mid-execution |
| 2-6 related questions, sequential | C · `creating-wizards` | `AskUserQuestion` (batched) | Onboarding flow, config wizard |
| 3-8 related pre-computed decisions | D · `hitl-decision-matrix` | `AskUserQuestion` (batched) + persistence | Morning-brief lock-in, strategy decisions |
| Typed or multi-field intake | E · `creating-form-input` | `AskUserQuestion` (form schema) | A filename, a threshold, a free-text label |

**A wizard is not a separate tool.** Routes C, D and E are all batched `AskUserQuestion` calls on whichever schema you were handed. The genuinely distinct primitive is Route B, which blocks a single tool call rather than the whole turn. These two facts do not conflict: batching is a property of HOW a route is delivered, while the route names WHAT you are building. Route E is a distinct shape — typed, multi-field intake — and it is also delivered as a batched call, exactly as C and D are.

If no route fits cleanly, **the shape is wrong** — re-scope before asking. Symptoms of a wrong-shaped interaction: >4 options, >12-char header, operator needs to read >40 seconds before answering, question doesn't end with `?`, nested wizard, lost context between questions.

## Universal quality gates (applied to every route)

These gates are PARAMOUNT — every interactive call in GRIP is auditable against them, and violations are caught by `lib/hitl_quality_gates.py`, anchored by `tests/test_hitl_quality_gates.py` (65 tests) and enforced live by `hooks/hitl-quality-hook.py`. Gates marked **choice only** do not apply to a `text` or `number` question, which carries no options at all.

| Gate | Rule | Failure mode if violated |
|---|---|---|
| Header length | ≤12 chars | UI truncation — operator misreads topic |
| Option count (**choice only**) | 2-4 options | Decision fatigue / insufficient resolution |
| Options absent (**typed only**) | A `text` or `number` question carries no options | A chooser rendered with nothing in it |
| Range present (**number only**) | `min` and `max` required, `defaultValue` inside them | A numeric control the UI cannot render honestly |
| Bidirectional | Every Q teaches + collects | One-sided extraction — operator doesn't learn anything |
| No "Other" mention | UI provides it automatically | Redundant text, distracting |
| Question ends with `?` | Regex `\?$` on question text | Statement-as-question confuses the parsing |
| Defer always available | Every question offers a path that doesn't force a decision | Over-collection of low-confidence answers |
| Persist or discard explicitly | Every answer goes somewhere durable or is explicitly discarded | Phantom state — operator "answered" but answer evaporates |
| ELI5 for multi-decision flows | Preamble required before first question in a wizard or matrix | Operator defers out of "I don't know what I'm deciding" |
| Cascade detection for Other-path reframes | When operator's free-text answer invalidates a downstream default, detect + rewrite | Persisted state contains contradictions |

Main-agent and MCP routes both honour these gates. `elicitation/create` JSON schemas map directly: `title` ↔ question, `properties` ↔ options, `required` ↔ forced-pick.

## Route specifications

### Route A — Single question (main agent)

Delegate to `asking-users`. Canonical invocation is a single `AskUserQuestion` call with one question.

- Use when: one decision point, operator has enough context, 2-4 meaningfully different outcomes.
- Don't use when: any confidence gate is ambiguous → escalate to wizard to build context first.
- Cross-ref: `skills/asking-users/SKILL.md`.

### Route B — Single question (MCP server mid-execution)

Delegate to `mcp-elicitation`. Uses the `elicitation/create` protocol inside an MCP tool handler.

- Use when: an MCP tool needs operator input that the original invocation didn't supply.
- Don't use when: the question could have been asked before the tool call started → lift to main agent, Route A.
- Cross-ref: `skills/mcp-elicitation/SKILL.md`.

### Route C — Wizard (2-6 related questions, sequential context)

Delegate to `creating-wizards`. Questions are batched per-call (≤4 per `AskUserQuestion`) but collected sequentially across calls when context evolves between batches.

- Use when: onboarding, configuration, progressive disclosure across steps, each answer informs the next question.
- Don't use when: questions are independent and pre-computed — use Route D instead.
- Cross-ref: `skills/creating-wizards/SKILL.md`, `skills/asking-users/SKILL.md`.

### Route D — Decision matrix (3-8 pre-computed decisions)

Delegate to `hitl-decision-matrix`. Each decision has a pre-computed default + rationale + falsifier; operator accepts, overrides, or defers.

- Use when: post-capture morning-brief lock-in, strategy-decision sprints, ADR batches, quarterly planning decisions.
- Don't use when: decisions have no pre-computed defaults — run a wizard (Route C) first to build the defaults, then a matrix (Route D) to lock them.
- Cross-ref: `skills/hitl-decision-matrix/SKILL.md`, `skills/decision-locking/SKILL.md`, `skills/decision-lifecycle/SKILL.md`.

### Route E — Form input (typed, multi-field)

Delegate to `creating-form-input`. Available only on the form schema, where a question may carry `kind: "text"` (a text box, no options, takes a `placeholder`) or `kind: "number"` (a numeric control requiring `min` and `max`).

- Use when: the answer is genuinely a value rather than a selection — a filename, a threshold, a count, a free-text label — or when several such fields are filled in one pass and submitted together.
- Don't use when: the plausible answers can be listed. The binary's own guidance is "Prefer choices whenever the likely answers can be listed", and reaching for a text box because listing options is hard work means the question is under-scoped.
- House rule: every option carries a description even though the form marks it optional. See the skill for why the schema's steer produces bare labels.
- Cross-ref: `skills/creating-form-input/SKILL.md`, `skills/asking-users/SKILL.md`.

## Pre-dispatch checklist

Before invoking this skill, confirm:

- [ ] I actually need operator input (vs. guessing with explicit uncertainty vs. executing with known defaults).
- [ ] If this is a `(yours)` closer, it is an ask too: it passes the owner test in `rules/canonical-output-style.md` (irreversible, fork, information gap, commercial, authority, danger), and anything else I can do myself is done this turn instead (V>> 2026-10-10).
- [ ] I have framed the question(s) in plain language — no jargon, no metric names, no acronyms.
- [ ] I know the shape — 1 question? N-question wizard? N-decision matrix? MCP mid-execution?
- [ ] I know the persistence target — session-only memory, durable file, hypothesis engine, decision-locking.
- [ ] I am within the interactivity budget for this session (operator fatigue is real — 3-4 interactive flows per 60-min session is roughly the ceiling).

## Post-dispatch checklist

After the interactive flow completes:

- [ ] Every answer is persisted or explicitly discarded.
- [ ] Every `Defer` answer has a re-prompt scheduled or an explicit "will not re-ask" note.
- [ ] Every reframe (`Other`-free-text) has triggered cascade detection and downstream defaults are rewritten.
- [ ] Hypotheses registered for any new falsifiable claims surfaced by the interaction.
- [ ] If the flow is likely to repeat, the specific shape is codified (new skill, named section in existing skill, or checklist).

## Migration note (existing integrators)

Integrators built before 2026-05-21 that invoke `/asking-users` or `/creating-wizards` directly (bypassing any "must route through `/hitl`" assertion that the v1.0.0 framing implied) **continue to work without change** under the DUAL-PARAMOUNT v2.0.0 model. The primitives ARE PARAMOUNT first-class entry points. **No migration of existing integrators is required.** The quality-gate contract still applies (it always did — the primitives implement it). The only change is that the contract is now explicit at both the primitive and dispatcher layers, rather than implicit in the dispatcher alone.

## Anti-patterns

| Anti-pattern | Why bad | Correct approach |
|---|---|---|
| Direct `AskUserQuestion` call that VIOLATES the quality gates (no header limit check, >4 options, no teaching, "Other" mentioned) | Bypasses the universal quality contract; ad-hoc asks degrade operator experience | Direct primitive use is fine — invoke `/asking-users` or `/creating-wizards` directly. The dispatch contract still applies: gates must pass. Consult `/hitl` if uncertain about which primitive or whether gates pass. |
| Using Route A (single question) when Route D (matrix) is the right shape | Operator answers N times in isolation, misses the coherent-set view | Batch into matrix; operator sees all decisions in context |
| Using Route D (matrix) for a single decision | Over-scaffolds trivial questions; wastes operator time | Route A |
| Skipping cascade detection in Routes C + D | Produces contradictions in persisted state | Always detect cascades on reframe answers |
| Asking for confirmation after operator already confirmed | Over-gating, signals distrust of own state | If consent is already on record in session, don't re-ask |
| A `(yours)` closer for a step the agent can take itself ("tell me to watch the PR and I'll merge it", "run this command", "approve the merge") | It ends the turn and pages the operator for work the agent was already authorised to do -- an ask that skipped every gate here (V>> 2026-10-10) | Do the step this turn and report it as `(mine)`; keep `(yours)` for the owner test in `rules/canonical-output-style.md` |
| Treating `elicitation/create` as a different pattern from `AskUserQuestion` | Duplicate quality rules, drift between surfaces | Same gates, different primitive |
| Persisting tentative language ("perhaps", "maybe", "??") as commitment | Misrepresents operator intent in persisted state | Register as falsifiable hypothesis, flag tentativeness explicitly |

## Relationship to other PARAMOUNT skills

`/hitl` is a **non-subsuming dispatch-contract** layer atop two **PARAMOUNT primitives** (`/asking-users`, `/creating-wizards`) plus three specialised siblings (`/mcp-elicitation`, `/hitl-decision-matrix`, `/creating-form-input`). "Non-subsuming" is load-bearing: the primitives are NOT subordinated to `/hitl`, they remain first-class PARAMOUNT entry points. `/hitl` sits *beside* them as a routing+gate cross-reference, not *above* them as a wrapper. It sits in the same family as the other cross-cutting dispatchers:

- `/ultrado` — dispatch surface over autonomous self-improvement loops
- `/hitl` — dispatch surface over interactive primitives (this skill)
- `/recall` — the layered cascade over retrieval primitives (Rule 0, Recall First)
- `/asking-users` — **PARAMOUNT primitive**; the single-question main-agent invocation. May be called directly.
- `/creating-wizards` — **PARAMOUNT primitive**; the multi-step wizard meta-skill. May be called directly.
- `/creating-form-input` — the form-schema contract; the typed-input sibling. May be called directly.
- `/context-gate` — the pre-tool-use blocker that catches gate violations

`/hitl` documents the routing+gate contract over `/asking-users` and `/creating-wizards`. The primitives stay sharp and single-purpose AND remain PARAMOUNT; the contract ensures the gates apply uniformly whether the agent reaches a primitive directly or via this dispatcher. DUAL-PARAMOUNT: contract and primitives both load-bearing, neither subsumed.

## Relationship to the confidence gate (Rule 7)

Rule 7 (PARAMOUNT, `rules/efficiency-rules.md`): "When confidence <99.9999999% → HALT and AskUserQuestion". The correct interpretation with `/hitl` in place:

> When confidence <99.9999999% → HALT and route through `/hitl`, which will invoke `AskUserQuestion` (or the right alternative) via the right route.

`/hitl` does not replace Rule 7 — it makes Rule 7 mechanical. The gate fires, dispatch resolves to a route, the route executes, persistence completes.

## Invocation patterns

### Auto-invoked (preferred)

Agent detects interactive need, mentally classifies shape, reaches for this skill's dispatch logic, executes the appropriate route. No explicit `/hitl` command needed — the skill acts as a mental checklist, not a hard gate.

### Explicit invocation (when operator asks)

Operator types `/hitl` with context to force the dispatcher. Useful when:

- Operator wants to see the routing choice narrated (debugging interactivity failures)
- Operator is building a new flow and wants quality-gate feedback on it
- Operator is auditing recent interactive flows for gate compliance

### Codified flow (skill + data)

For repeating matrices (e.g. quarterly strategy, sprint planning), persist the specific matrix shape as its own skill under `skills/` that invokes `hitl-decision-matrix` with a fixed question set. The parent `/hitl` dispatch still applies for the meta-level quality check.

## Falsification of this skill

This skill claims: "Unifying all HITL dispatch under one PARAMOUNT skill produces more coherent operator experience than ad-hoc `AskUserQuestion` calls + scattered sub-skills."

Falsified by:
- Operators report the routing overhead (mental classification step) exceeds the saved time from consistent quality gates
- Dispatch errors (wrong route picked for a given shape) exceed 10% of interactive calls — suggests the route taxonomy is wrong
- New interactive surfaces emerge (voice, multi-modal, VR) that don't fit the 4-route taxonomy — suggests the taxonomy is over-fitted to 2026 primitives
- The quality gates prove individually falsifiable in ways that invalidate the unified enforcement premise (e.g. ≤12-char headers constrain legitimate questions unnecessarily)

## Changelog

| Version | Date | Changes |
|---|---|---|
| 2.1.0 | 2026-10-10 | A `(yours)` closer is an operator ask: one checklist line and one anti-pattern row bind it to the owner test in `rules/canonical-output-style.md` (V>> operator correction). No route, gate or primitive changed. |
| 2.0.0 | 2026-05-21 | DUAL-PARAMOUNT clarification (council-ratified): `/hitl` reframed as dispatch-contract documentation; `/asking-users` and `/creating-wizards` confirmed as PARAMOUNT primitives that may be invoked directly. No PreToolUse hook. No migration of existing direct-primitive integrators. Anti-pattern reframed: direct primitive use is legitimate; only gate-violating ad-hoc calls are anti-pattern. |
| 1.0.0 | 2026-04-18 | Initial PARAMOUNT skill, unifies `asking-users` + `creating-wizards` + `mcp-elicitation` + `hitl-decision-matrix` dispatch |
