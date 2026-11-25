---
name: create
description: Crystallise a PROVEN achievement into the MINIMAL reusable substrate artifact (skill / agent / rule-principle / playbook / workflow), gated by the knowledge-maturation-functor + an anti-theatre/exogenous criterion. Use after an /auto /rsi /dogfood-domain loop produces an achievement worth making repeatable + autonomously-executable + self-improving — NOT to mass-generate machinery. Thin orchestrator over creating-skills, agent-auto-creator, and rules/knowledge-maturation-functor.md.
---

# /create — achievement → reusable substrate (disciplined)

Turn a loop achievement into the **one** substrate artifact that makes it
**(a) default, (b) autonomously executable, (c) self-improving** — without
falling into the trap GRIP's own council named: *internal machinery is the most
seductive theatre.* `/create` is the knowledge-maturation-functor made on-demand,
with the anti-theatre gate as its KERNEL.

## The kernel gate (run FIRST — refuses more than it emits)

Before creating anything, answer all four. **Any "no" → do NOT create; stop at
memory.** This gate is the point of the skill; the artifact is secondary.

1. **N≥ threshold? (maturation functor)** — has this achievement *independently
   recurred*? `rules/knowledge-maturation-functor.md`: memory@N=1, rule@N=1-if-severe,
   **skill/agent/shim@N=2 independent instances**. A one-off (N=1) becomes a
   *memory*, never a skill. Mass-generating a skill per achievement is the exact
   Goodhart the council flagged — **forbidden**. The count is two rather than three
   only because each instance must carry more evidence: the first implementation is
   built with a human in the loop (`/hitl`), then tested, monitored and ultralearned,
   and it promotes automatically only once it proves its quality, value and usefulness.
2. **Non-duplicative?** — does an existing skill/agent/rule already cover it?
   (GRIP-First + Rule 21.) If yes → extend it, don't spawn a sibling.
3. **External-leaning or load-bearing-internal?** — does crystallising this move
   external value (a user, revenue, a shipped product surface) OR remove a
   provably-recurring internal failure? Pure self-admiring machinery (it only
   makes GRIP feel more powerful) → **do not create** (`rules/loop-priority-external-over-theatre.md`).
4. **Carries a falsifier?** — can you state what observation would prove the
   artifact worthless? No falsifier → it's a monument, not a maturing rule
   (functor invariant: the falsification clause travels at every stage). **Required.**

> The gate's job is to make `/create` emit RARELY. A session that runs `/create`
> ten times and emits one artifact is working correctly. Ten artifacts is the
> failure mode.

## Artifact-type router (only after the gate passes)

| Achievement shape | Artifact | Built via (compose — never rebuild) |
|---|---|---|
| A reusable *procedure* an operator/loop invokes | **skill** | `skills/creating-skills` + `skill-creation-best-practices` (PARAMOUNT gate) |
| A reusable *specialist reasoner* (review/judge/enforce) | **agent** | `skills/agent-auto-creator` |
| A *"always do X / never Y"* recurring doctrine | **rule/principle** | `rules/*.md` (+ CLAUDE.md row); severe → rule@N=1 |
| A *named multi-step recipe* (sequence of existing tools) | **playbook** | a doc in `docs/` or a composite skill; no new code |
| A *deterministic fan-out/pipeline* over many items | **workflow** | the `Workflow` tool script; only if genuinely parallel |

## The (a)/(b)/(c) contract each emitted artifact MUST satisfy

- **(a) Default** — auto-loaded/auto-surfaced: a rule lands in `rules/` (auto-loaded);
  a skill is registered + indexed; the next session inherits it with zero recall.
- **(b) Autonomously executable** — invocable by an autonomous loop without a human:
  named trigger + no mandatory HITL on the happy path (quality gates still fire).
- **(c) Self-improving** — carries its falsifier + a tracked metric, so it is
  *improved by being falsified*, not by accreting. (This is the only honest form
  of "recursively self-improving" — Popperian, not auto-recursion theatre.)

## Dogfood + recursion (bounded)

`/create` is dogfoodable: its FIRST output was `rules/loop-priority-external-over-theatre.md`
— the principle that governs the very loops that produce achievements, including
`/create` itself. That is the *only* recursion worth having: the artifact reflects
back on the process. There is **no** auto-recursive "engine that builds engines" —
that is the theatre. Recursion = the governing rule applies to its own author.

## Falsification (of this skill)

`/create` is wrong if, over the next ~10 achievements: it emits artifacts at N<2
that then go unused (skill-spam — measure: emitted-artifact use rate); OR the gate
never once refuses (it's a rubber stamp, not a gate); OR a `/create`d artifact's
value is asserted by artifact *count* rather than external/recurring use (Goodhart).
Track: emit:refuse ratio (target heavily weighted to refuse) + per-artifact
external-or-recurring-use within 30 days. Goodhart-safe: success = an artifact
*used*, never an artifact *created*.

## Relationship

Composes `creating-skills`, `agent-auto-creator`, `skill-creation-best-practices`;
governed by `rules/knowledge-maturation-functor.md` (the maturation ladder) and
`rules/loop-priority-external-over-theatre.md` (the anti-theatre criterion). Sits
beside `meta-improvement-switch` (when to pause project work to improve substrate)
— `/create` is the *act* of crystallising once you've decided to.
