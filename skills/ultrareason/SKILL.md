---
name: ultrareason
description: Answer a hard reasoning question grounded in the GRIP moat (what GRIP has verified + been contradicted on) via a cost-tiered escalation ladder on Pool-B ($0, off the weekly Anthropic limit) — a cheap single-expert pass first, escalating to the sovereign council only if a confidence criterion is unmet. Use for a genuine reasoning/decision question where GRIP's own verified knowledge should ground the answer and cost should scale with difficulty. NOT for external research (use research-council), code review (ultrajudge), or a generic iterate-until-done loop (converge).
disable-model-invocation: true
---

# /ultrareason — moat-grounded, cost-tiered reasoning

## What it is (plain English)

Ask a hard question. Instead of firing a full expensive council at everything,
`/ultrareason`:

1. **Grounds** the question in the **moat** — it looks up what GRIP has actually
   *verified* (the `lib/recall` index) and what GRIP has been *proven wrong on*
   (the RED chain), and injects those facts into the reasoning prompt. The answer
   starts from GRIP's real knowledge, not a blank slate.
2. **Reasons cheap first** — a single Pool-B expert pass (a cheap non-Anthropic
   model via HAL `grip_infer`).
3. **Escalates only if needed** — if the cheap answer is thin or hedging (fails a
   confidence check), it escalates *once* to the full **sovereign council**
   (multiple non-Anthropic speakers + an exogenous judge + a sealed record). If
   the cheap answer is already confident, it stops — no council, no cost.
4. **Returns the answer with its provenance** — which moat facts grounded it,
   which tier answered, whether it escalated.

The escalation ladder is the `lib/converge` kernel; the experts are the existing
Pool-B primitives. Nothing new is reasoned from scratch — this is the wiring.

## Why it is distinct (not a duplicate)

| Skill | Does | `/ultrareason` differs by |
|-------|------|---------------------------|
| `research-council` | deep research on **external** tools/papers | grounds in the **internal moat**, not the web |
| `broly` / `broly-meta-agent` | recursive meta-reasoning | **cost-tiered** (cheap→council), moat-grounded |
| `converge` | generic iterate-until-criterion | *uses* converge as its ladder; adds grounding + experts |
| `ultrajudge` | judge a deliverable | answers a **question**, not a review verdict |

The non-duplicative core: **moat-grounding + cost-tiering.** No other skill routes
reasoning through the recall substrate or scales cost with difficulty.

## Usage

```bash
/ultrareason <your question>
# programmatic:
python3 -c "from lib.ultrareason import ultrareason; import json; \
  print(json.dumps(ultrareason('should X or Y?'), indent=2))"
```

Returns: `{answer, tier (cheap|council), escalated, grounded, satisfied, iterations,
grounded_on (moat provenance), moat_facts, contradictions}`.

**Grounded or not.** The facts come from `/recall`'s search hits (titles and paths
only, never file bodies, ranked first) and the moat's verified records; a verified
record replaces a hit on the same path. The provenance card counts the two apart,
because a hit is a match, not a verified fact. The model is told to begin with
`NOT GROUNDED` when the facts do not answer the question, and never to invent names,
numbers or models. Only a line that begins with `NOT GROUNDED`, or an admission that
the facts are irrelevant or do not answer, counts. With facts to hand such an answer
escalates to the council; with no facts it stops, since the council would have nothing
more. `grounded` is always present, and `false` when the final answer still says so or
there were no facts. Never present an answer with `grounded: false` as something GRIP
knows (GRIP#9543, GRIP#9562).

**After presenting the answer, render the provenance card.** It shows WHY the
answer is trustworthy — which verified moat facts grounded it, which tier
answered, and (for an escalated answer) the council's sha256 seal + re-verify
command:

```python
from lib.ultrareason import ultrareason, render_provenance_card
r = ultrareason("should X or Y?")
print(r["answer"])
print(render_provenance_card(r))   # the delight surface of the provability moat
```

The card is operator-facing; its moat-fact paths are internal — do not publish it
on an external surface.

## Sovereignty (Pool-B, $0)

Both tiers run on **Pool-B** — HAL `grip_infer` (cheap) and
`bin/grip-sovereign-council` (escalation), which are non-Anthropic and **off the
weekly Anthropic limit** (`rules/pool-a-offload-routing.md`). The exogenous judge
in the council keeps the verdict honest (`rules/exogenous-anchor-law.md`). This is
the load-bearing reason to build it: heavy reasoning that does NOT burn Pool A.

## Design (for maintainers)

- Orchestration is **pure over four injected callables** (`ground` / `cheap` /
  `council` / `criterion`) so it is testable without HAL or network
  (`tests/test_ultrareason.py`, 10 tests). The real defaults are thin, **fail-open**
  wrappers — any tier failure degrades to the best answer already in hand.
- The ladder is `lib/converge.converge(state, criterion, improve, max_depth)`
  (`rules/loop-engineering.md`): improve = escalate one tier; criterion = the
  answer is confident enough.

## Honesty: maturity + falsifier

- **N=1 (operator-elected).** Per `/create`'s gate this capability has not yet
  recurred 3× independently — it is built now because the operator directed it and
  it is load-bearing (Pool-B offload). It earns its keep by the falsifier below,
  not by existing.
- **Falsifier — delete this skill if, over the next ~10 uses:** moat-grounding
  never changes an answer vs an ungrounded pass (grounding is inert → drop the
  grounding, or the skill); OR the ladder never once stops at the cheap tier *when
  the cheap tier is HEALTHY* (it always escalates → the cheap tier is pointless,
  just the council with overhead — a cheap-tier OUTAGE correctly escalates and is
  excluded from this count, so an infra outage never triggers deletion); OR it is
  never used (no demand). Track: escalation rate (target
  0<rate<1 — both tiers earn their place), grounded-vs-ungrounded answer delta,
  invocations/month.

## Relationship

Composes `lib/recall` (grounding), `lib/converge` (ladder),
`bin/grip-sovereign-council` + HAL `grip_infer` (Pool-B experts). Governed by
`rules/pool-a-offload-routing.md` (Pool-B default), `rules/exogenous-anchor-law.md`
(exogenous judge), `rules/loop-engineering.md` (the kernel), and `/create`'s
maturation gate (the N=1 caveat above).
