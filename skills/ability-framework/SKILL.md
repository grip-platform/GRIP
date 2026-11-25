---
name: ability-framework
description: TOMBSTONE — the ABILITY concept (a persistent, self-improving knowledge substrate, distinct from a static SKILL) is preserved here for cheap re-derivation, but the runtime was never built (N=0). Not active. See revival condition below.
status: Tombstone
effort: low
version: 1.1.0
---

# GRIP Ability Framework — TOMBSTONE

> **Status: tombstoned 2026-05-17 by ratified council verdict**
> (`drafts/council-skills-architecture-verdict-2026-05-17.md`, decision D2).
> Reduced from an "Active" skill to a concept tombstone. The dead machinery
> (RSI trigger runtime, `optim.sh abilities` runner, outcome-recorder, the
> three seeded job-search ability JSONs) was **deleted**, not because the
> idea is bad but because it was **never executed once in three months**
> (N=0: no runner, no writer, all instances `usage_count:0`, frozen since
> 2026-02-23). Carrying a self-improving substrate that never improved is
> YAGNI debt.

## The preserved concept (re-derivable kernel — do not delete)

> A **SKILL** defines *how to do something* (a static reference protocol,
> `skills/*/SKILL.md`, human-updated, stable across sessions). An
> **ABILITY** would accumulate *what was learned from doing it* (a dynamic
> per-domain state substrate that evolves with use). Skills are the verbs;
> abilities would be the memory of having used them.

That one paragraph is the entire idea. It is kept so the option can be
re-derived in minutes if it is ever revived — no need to reconstruct it
from a corpse.

## Why it was tombstoned, not revived

A grounded five-perspective council (verdict §2, ratified by V>> 2026-05-17)
found, with five independent derivations, that reviving it now would:

1. **Reintroduce the named self-scoring failure mode** — its YSH trigger
   fires on `outcomes.length`, and outcomes were self-recorded by the same
   session: a closed loop judging its own work with an edge inside its own
   causal cone (`rules/exogenous-anchor-doctrine` candidate /
   `feedback_never_self_score.md`).
2. **Fork the PARAMOUNT, active maturation story** —
   `rules/knowledge-maturation-functor.md` (observation→memory→rule→shim)
   is already GRIP's canonical "what was learned" ladder. Two maturation
   systems is a DRY violation at the architecture level.
3. **Repeat its own history** — it died once by "the elegant design shipped,
   the boring glue did not". Reviving it means building exactly that
   never-built glue, with zero evidence the manual→automatic transition is
   tractable.

## Revival condition (the only door back in)

This is re-openable **only** via a fresh *meta-deliberation* that supplies
**new exogenous grounding** that `knowledge-maturation-functor.md` is
genuinely *inadequate* for stateful skill improvement — a claim this
single-model council, sharing the functor's authoring cone, explicitly
flagged it could not itself adjudicate (verdict §6 falsifier 3). Absent
that exogenous reason, the functor ladder owns this problem.

## What learning uses instead, today

`rules/knowledge-maturation-functor.md` + KONO/`insights.jsonl` +
`/anamnesis` (spared by the same verdict, decision D3 — it has a VERIFIED
real closed-loop win and is test-covered). `abilities/schema.json` is
retained as the concept's formal shape; it is not loaded by anything.
