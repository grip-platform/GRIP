---
name: hitl-decision-matrix
description: Interactive human-in-the-loop decision matrix wizard. Converts N pre-computed decision defaults (with rationale + falsifier) into a batched AskUserQuestion flow with ELI5 framing, cascade detection, confirmation step, and durable persistence (vision doc + hypothesis engine). Use when multiple related decisions need operator lock-in as one coherent conversation rather than N separate questions.
allowed-tools: AskUserQuestion, Edit, Write, Read, Bash
---

# HITL Decision Matrix

Converts a pre-computed morning-brief / decision-list into a confirmed, persisted set of locked decisions with full audit trail. Every decision teaches (default + rationale + falsifier) and collects (accept / override / defer).

Sibling of `asking-users` and `creating-wizards` (they are the primitives; this is the *flow*). Invoked by the PARAMOUNT unified `hitl` skill as one of its routes.

## When to use

- Multiple related decisions (typically 3-8) that share a common subject and need to be confirmed as a coherent set.
- Each decision already has a pre-computed recommendation (default + rationale + falsifier) that the operator only needs to accept, override, or defer.
- The decision outcomes must persist beyond the session — a doc append, hypothesis registration, or decision-locking entry is part of the flow.
- Example triggers: post-capture "morning brief" D1..D6 lock-in; product-vision decision sprints; strategy-decision locking after red-team exercises; ADR batches on a feature.

## When NOT to use

- Single-question prompts — use `asking-users` directly.
- Open-ended exploratory questions with no pre-computed default — the matrix pattern presupposes operator time is more valuable than designer time; that inverts if defaults aren't worked out first.
- One-shot commands with unambiguous intent — execute, don't ask.

## The five-phase flow

### Phase 1 — ELI5 framing (prose preamble)

Before asking anything, give the operator the "what are we actually deciding, in plain terms" view. Table format is ideal. Every decision gets one row: `#`, short plain-language question, default in plain English.

Anti-patterns in this phase:
- Jargon, acronyms, internal metric names — translate via `plain-language` skill conventions.
- Over-long preamble — if it takes more than 40 seconds to read, the operator disengages before answering.
- Mentioning "Other" — users see it automatically in AskUserQuestion UI.

The preamble ends with an explicit sentence describing the shape of choices available (typically "Accept / Override / Defer" per decision).

### Phase 2 — Batched AskUserQuestion calls

Group decisions into batches of 3 (AskUserQuestion supports 1-4 questions per call). 3 is the sweet spot: enough for momentum, few enough to fit on one screen without scroll fatigue.

Batch boundaries should respect logical independence — if D4 depends on D3's answer, they go in different batches so Phase 4 (cascade detection) can rewrite D4 before asking it.

Per-question shape (canonical ternary):

```text
Question: "D{N} — {plain-language question}. Default: {default in one sentence}. Falsifier: {what would invalidate the default}. What do you want?"
Header: "D{N} {1-3-word topic}"   # ≤12 chars total
Options:
  - "{default label} (Recommended)" — {what happens, 10-25 words}
  - "{override label}" — {what changes if chosen}
  - "Defer — {reason to defer}" — {what 'defer' means for this decision}
```

Four-option variant (acceptable when override has two meaningfully different shapes):

```text
Options:
  - "{default} (Recommended)" — ...
  - "{override variant A}" — ...
  - "{override variant B}" — ...
  - "Defer" — ...
```

Never go above 4. Decision fatigue is real.

### Phase 3 — Capture + classify answers

For each answer:

| Operator response | Classification | Next action |
|---|---|---|
| Accepts recommended option | `locked` | Record default + rationale + falsifier |
| Picks named override option | `locked (override)` | Record override + update downstream defaults if it invalidates them |
| Picks Defer | `deferred` | Record as pending; no persistence of a default; schedule re-prompt |
| Picks "Other" with free-text reframe | `reframe` | **Cascade detection mandatory** — see Phase 4 |

A `reframe` answer can invalidate pre-computed defaults for *subsequent* decisions. If D{N} is reframed, re-examine D{N+1}..D{last} before asking them.

### Phase 4 — Cascade detection (PARAMOUNT)

This is the step that separates a real HITL matrix from a mechanical survey. When a reframe lands:

1. **Explicitly acknowledge the reframe** in prose. Summarise what the operator's "Other" actually changed, in the same plain language the preamble used. This is a courtesy but also a falsification check — if the operator sees their reframe misquoted, they catch it immediately.
2. **List the cascade** — which not-yet-asked decisions had defaults that relied on assumptions the reframe invalidated. Be specific: "D4 default 'ships with GRIP' assumed no monetizable surface; streaming reframe adds one, so D4 default is now stale."
3. **Rewrite the affected defaults** before asking them. The downstream batch asks the revised questions, not the original ones. Label them "(revised given D{N} {reframe topic})" in the question text so the operator knows they're not reading the same options as the morning brief.
4. **Flag secondary implications** that aren't in the decision matrix but need recording elsewhere (e.g. a superseded hypothesis whose supersession note is now partially inaccurate, a sprint scope that just grew, a new hypothesis candidate). These go into the Phase 5 persistence step as "cascading implications" — not decisions but state changes that follow from decisions.

Cascade detection is the difference between "rubber-stamped matrix" and "meaningfully converged matrix". Skip it and the persisted state ships a contradiction.

### Phase 5 — Confirmation + persistence

One final AskUserQuestion with two questions (or one, depending on scope):

1. **Where to persist?** Options typically include: (a) vision doc append + hypothesis engine register/amend, (b) dedicated decision record file via `decision-locking`, (c) hypothesis engine only, (d) vision doc only. Default (a) for Observatory-class decisions; (b) for formal ADR-style decisions.
2. **Codify flow?** If this matrix seems reusable, ask whether to promote the specific shape (questions + defaults) to a reusable skill or checklist. Usually skip for one-off matrices; opt in when the matrix is likely to repeat (sprint planning, quarterly strategy).

Persistence writes:

- **Vision doc append**: `## Decisions Locked (YYYY-MM-DD via HITL decision matrix)` section with a table of outcomes + cascading implications + process provenance paragraph.
- **Hypothesis engine**: `lib/hypothesis_engine.py register(...)` for new falsifiable claims surfaced by the matrix. Amend existing hypotheses via direct JSONL edit of the `notes` field when `save_hypotheses` is unavailable.
- **Decision-locking** (optional): invoke the `decision-locking` skill to create a formal decision record with owner + date + falsifier.

## Implementation -- `lib/hitl_decision_matrix.py` (2026-10-03)

Phases 2, 4 and 5 are code, not narration. The asking (phase 2's `AskUserQuestion`
calls) stays with the harness; everything around it is mechanical:

| Phase | Code | What it guarantees |
|---|---|---|
| 1 | `preamble(matrix)` | one plain-language row per decision, the accept / override / defer sentence |
| 2 | `matrix.batches()` + `questions(batch)` | at most three per batch; a decision never shares a batch with one it `depends_on`; every payload passes `lib/hitl_quality_gates.py` before it is asked |
| 3 + 4 | `matrix.record(Answer(...))` | the answer is classified (`locked`, `locked (override)`, `deferred`, `reframe`) and the cascade is computed: assumptions are TAGS -- a decision's default `settles` tags, later defaults `assumes` tags; an accepted default settles its tags, a reframe or override settles only what the caller `kept_settles`, a deferral settles nothing; every unasked decision whose `assumes` meets a lost tag, or that `depends_on` the answered one, is reported STALE. `matrix.revise(id, default=..., default_detail=...)` rewrites it and the question is labelled `(revised given D{N} <topic>)`. A matrix with no tags is told so (`report.untracked`) rather than told "no cascade". |
| 5 | `persist(matrix, vision_doc=..., register_fn=...)` | appends the `## Decisions Locked (YYYY-MM-DD via HITL decision matrix)` section (outcomes table, cascading implications, process provenance); registers only decisions carrying a `HypothesisSpec` through `lib/hypothesis_engine.register` (injectable), tentative ones with `[tentative, D{N}]` in the claim; deferred decisions persist no value |

Specs are JSON (`lib/hitl_decision_matrix.py spec.json --preamble --batches` renders them
for a dry run before a sitting). Pinned by `tests/test_hitl_decision_matrix.py`.

## Integration with other skills

| Skill | Relationship |
|---|---|
| `asking-users` | Primitive — every Phase 2 question is an `AskUserQuestion` call |
| `creating-wizards` | Pattern library — defines anti-patterns, header length, option count rules this skill inherits |
| `mcp-elicitation` | Alternate primitive for MCP servers — substitutes `elicitation/create` for `AskUserQuestion` when inside an MCP tool handler |
| `decision-locking` | Downstream — formalises matrix outputs as tracked decisions |
| `decision-lifecycle` | Downstream — records decision state over time, enables future audits |
| `hypothesis-driven-development` | Downstream — registers new hypotheses surfaced by the matrix |
| `plain-language` | Upstream — provides the translation layer for Phase 1 ELI5 framing |
| `hitl` (unified PARAMOUNT) | Parent — routes to this skill when the task shape is "N related decisions to lock" |

## Anti-patterns

| Anti-pattern | Why bad | Correct approach |
|---|---|---|
| Asking all N decisions one at a time | N round-trips, operator loses context between them | Batch 3 per AskUserQuestion call |
| Skipping Phase 1 ELI5 framing | Operator doesn't know the stakes; defers more than they should | Always provide the plain-language preamble first |
| Skipping Phase 4 cascade detection | Operator's override invalidates later defaults but they still get asked as-if unchanged; persisted state contains contradictions | Detect + rewrite defaults + label revised questions |
| >4 options per question | Decision fatigue, cognitive overload | Cap at 4; prefer 3 (accept / override / defer) |
| Mentioning "Other" in option text | Duplicated with UI; distracting | Never mention — UI handles it |
| Header >12 chars | Truncated in UI | Keep headers in `D{N} {topic}` form, 1-3 words |
| Persisting without a confirmation step | Operator can't "go back" if they realise a mistake mid-flow | Final AskUserQuestion for persistence location + codification choice |
| Registering monetization/speculation claims as committed | Operator used "perhaps ??" or similar tentative language; registering as committed misrepresents intent | Register as falsifiable hypothesis, not as commitment; explicitly note tentativeness in the Decisions Locked doc |

## Example: Observatory D1-D6 lock (2026-04-18)

Worked example — the canonical run of this skill. Full trace persisted in `plans/observability-product-vision.md` under "Decisions Locked (2026-04-18 via HITL decision matrix wizard)".

- **Preamble**: 6-row plain-language table + "Accept / Override / Defer" explanation
- **Batch 1**: D1 (brand), D2 (webcam), D3 (voice) — 3 questions, 1 call
- **Cascade**: D2 answered via "Other" with streaming reframe. Detected that D4 (commercial) + D6 (audience) defaults now stale.
- **Batch 2**: D4 (revised), D5 (unchanged), D6 (revised) — 3 questions, 1 call, all labelled with cascade provenance where applicable
- **Confirmation**: Persistence choice (vision doc + hypothesis engine) + codify-flow choice (create skill + unified `hitl` parent)
- **Persistence**: Vision doc appended, H113 supersession note refined, H114 notes amended, H116 (streaming viability) registered with 2026-05-18 deadline

Total elapsed: ~5 minutes of operator attention across 3 AskUserQuestion round-trips.

## Quality gates (machine-verifiable)

These are the checks a reviewer runs to validate a matrix run was well-executed:

- [ ] ELI5 preamble present before first AskUserQuestion call
- [ ] All questions batched into calls of ≤4 questions
- [ ] Every question has: plain-language text, default, falsifier, 3-4 options, ≤12-char header
- [ ] No question text mentions "Other"
- [ ] If any answer is "Other"-free-text (reframe), subsequent batches use revised defaults
- [ ] Final confirmation step before persistence
- [ ] Vision doc has "Decisions Locked" section with table + cascading implications + process provenance
- [ ] Hypotheses registered/amended via `hypothesis_engine.register` where applicable
- [ ] Tentative operator language ("perhaps", "maybe", "??") not persisted as commitment

## Falsification of this skill

This skill claims: "Batched AskUserQuestion with cascade detection produces more coherent locked decisions than N individual questions."

Falsified by:
- Operators report the ELI5 preamble adds friction without reducing question-time (i.e. preamble is noise, not signal)
- Cascade detection produces false positives (treats independent decisions as dependent) more often than true positives
- The 5-minute end-to-end duration exceeds operator tolerance for batches of 6+ decisions — re-batching into smaller chunks yields equivalent results with lower cognitive load
- "Defer" is chosen so often that the matrix becomes a procrastination ritual

## Changelog

| Version | Date | Changes |
|---|---|---|
| 1.0.0 | 2026-04-18 | Initial skill, extracted from Observatory D1-D6 worked example |
