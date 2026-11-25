# R0 — Critical Thinking Gate: Falsify Before Believing (PARAMOUNT)

Origin: 2026-04-22. V>> directive: "Falsify before believing anything you realise
as an insight is in fact true and factual." Hossenfelder-style critique. Rule 0.

## Scope: Every Prompt (not only ★ Insight blocks)

R0 applies to **every factual claim, generalisation, and recommendation** in every
assistant response — not just ★ Insight blocks. Insights are the highest-density
case, but the failure mode (post-hoc rationalization, scope drift, unfounded
universals) occurs in plain prose too.

**Zero-overhead inline protocol** — before stating any non-trivial assertion:

| Q | Ask | If no → |
|---|-----|---------|
| 1 Evidence | Can I point to a specific file, line, test, or observed output? | Qualify or drop |
| 2 Scope | Does my qualifier match what I actually observed? | Narrow the claim |
| 3 Falsifiability | What would make this wrong? | Add the condition or drop |

Cost: ~0 tokens. No hook. No scoring pass. Pure cognitive discipline.

Examples of R0 applied to plain prose (not insight blocks):

> ❌ "This pattern is cleaner." → no evidence, no scope, not falsifiable
> ✓ "In `auto-memory-hook.py`, scanning all turns (not just the last) recovered
>   5 insights from a session where 0 were captured before — falsified if dedup
>   catches duplicates across the full scan."

> ❌ "Rate-limit sentinel always prevents duplicate writes."
> ✓ "The sentinel in `lib/hal/provider_backoff.py` uses `O_EXCL` on write to
>   prevent concurrent writes within a process — this would fail under a race
>   between two separate processes writing simultaneously (not tested)."

## The Problem

When generating ★ Insight blocks, the dominant failure mode is post-hoc
rationalization: the insight is written AFTER an action, confirming the action
was correct rather than evaluating whether the conclusion is true. Single-session
evidence is mistaken for universal rule.

## The Six-Axis Test (Hossenfelder Adaptation)

Apply to every ★ Insight block before writing it:

| Axis | Core question | Fail condition → do not write |
|------|--------------|-------------------------------|
| 1 Testability | What observation would falsify this claim? | "I cannot think of any" → it is a platitude |
| 2 Technical Rigour | Is there a specific file, line, test, or measured output grounding this? | "It just makes sense" → unfounded |
| 3 Novelty | What does this say beyond what context already implies? | If it would be obvious to anyone, omit it |
| 4 Precision | Do key terms keep one meaning throughout? | "Anti-pattern" for any deviation → meaningless |
| 5 Scope Honesty | Does the claim match the evidence scope? | Single observed instance → universal rule → overgeneralization |
| 6 Red Flags | Post-hoc framing? Correlation stated as causation? "Obviously"? | Any red flag → revise before writing |

## Required Structure for Valid ★ Insight Blocks

Every ★ Insight MUST include at minimum:

1. **Specific evidence** — the file, line, test name, log output, or observed
   behavior that grounds the claim. Not "this suggests" — a concrete reference.

2. **Scope qualifier** for any generalized claim — "in this case", "for this
   pattern", "in this codebase", "when X holds". Universal "always"/"never"
   requires an explicit qualifying condition ("unless", "except when").

3. **Falsification condition** for the generalized part — "this would be wrong
   if Y". Specific empirical observations do not require this (they ARE the
   falsification: observable or not).

## Annotated Examples

### Invalid — fails Axis 1 and 5:

> "The silent sys.exit(0) on a missing file is an anti-pattern."

Axis 1 fail: What would falsify this? When a feature is optional, silent exit is
correct behavior. Axis 5 fail: claim stated universally from one specific case.

### Valid — passes all six axes:

> "When a REQUIRED dependency is missing and the hook must function to serve its
> purpose, sys.exit(0) without logging is an anti-pattern — learning-hook.py
> silently did nothing for months because learning_detector.py (underscore) was
> referenced as learning-detector.py (hyphen). This would be wrong if the hook's
> failure had no downstream effect, but the hook IS the real-time insight pipeline
> and its failure was completely invisible."

## Self-Falsification of R0

R0 is wrong if:
- Applying this protocol consistently REDUCES insight quality (too restrictive)
- The overhead prevents useful insights from being written at all
- The "scope qualifier" requirement produces verbose, unreadable insights with no
  improvement in accuracy

Track via: insight save rate per session, operator feedback on pruning rate,
ratio of overgeneralized claims in MEMORY.md before vs. after.

## Relationship to Other Rules

R0 is Rule 0 — it precedes and governs all other rules, including the Insight
generation format in `rules/efficiency-rules.md` Rule 16. Rule 16 says insights
must describe what the code DOES. R0 adds: they must do so with VERIFIED scope.

R0 does NOT negate Rule 16 — it tightens it.

R0 governs `rules/reasoning-lenses.md`. Anything may propose there, including a simple
answer, a child's question or a gut feel, and only what survives attack may decide.
R0's three questions are the first test of any such proposal, and the source of an
idea never relaxes them.

Narrative (origin, incidents, measurements): `docs/doctrine/r0-critical-thinking.md`.
