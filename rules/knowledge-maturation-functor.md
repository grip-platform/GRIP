# Knowledge-Maturation Functor (PARAMOUNT)

Origin: 2026-05-16. Names the pattern that already governs
`llm-augmentation-pattern.md`, `syscall-doctrine.md`,
`no-guardrail-evasion.md`, and `verify-canonical-not-claimed.md` — and
the verify-canonical session that traversed it end-to-end (rule #2745 →
bidirectional #2748 → mechanical hook #2756).

Amended 2026-10-03 by V>>: *"Binding at N=1, skill at N=2 (two). YSH should be
decreased now by 1. The first implementation should always be /hitl, tested,
monitored, ultralearned, and then if found to be of high quality, value and
usefulness, be promoted automatically."* The shim rung fell from N=3 to N=2.

## The functor

A lesson moves through stages of strictly increasing enforcement
strength. This is a one-way functor — each arrow is earned, never
skipped, never reversed casually:

```
observation → memory → rule → shim
```

| Stage | Home | Enforcement | Advances at |
|-------|------|-------------|-------------|
| observation | conversation | none (dies at session end) | — |
| memory | `projects/*/memory/*.md` | pull (fires only if queried) | N=1 |
| rule | `rules/*.md` (every session) | push; review + habit | **N=1** if severe (cheap, reversible) |
| shim | `lib/*.py` + a hook/gate | mechanical; deterministic; un-reason-around-able | **N=2 independent** instances, through the promotion gate |

## What co-varies

Along observation→shim these rise **together** (the **covariant**):
enforcement determinism; the evidence count N to advance; blast
radius; and the *distance the invariant is pushed from the point of
use* — from call-time agent judgement into a pre-call gate. A shim's
purpose: soundness **in the artifact, not per-callsite**.

These fall **inversely** (the **contravariant**): reversibility;
reliance on in-the-moment judgement; breadth claimable from one
instance.

The **invariant** the functor preserves at every stage: the
**falsification clause**. observation, memory, rule, shim all carry the
same "wrong if X". The functor transforms the *enforcement mechanism*
and preserves the *refutable claim*. A shim with no falsifier is not a
matured rule — it is an unfalsifiable mechanism (forbidden).

## The N-gate (what enforces the covariant)

> Rule now (N=1, if severe — cheap, reversible). Shim, skill or agent at
> **N=2 independent** instances, with the soundness invariant enforced
> **in the shim itself**, not per-callsite. Never abstract before proven 2×.

**The promotion gate** pays for the lower count. The first implementation
is always built with a human in the loop (`/hitl`), then tested, monitored
and ultralearned (an `/ultralearn` seal exists); it promotes automatically
only if it then proves its quality, value and usefulness. N=2 without the
gate is the Goodhart failure this functor exists to stop.

"Independent" = 2 distinct callsites, not 2 occurrences of one
instance-class. The grounded design-check is the **N-counter**: it
measures independence and refuses premature abstraction (verify-canonical
2026-05-16: 7 occurrences, N=1 instance-class → ship hook, defer lib).

## Instances (cross-ref, do not duplicate — DRY)

- `llm-augmentation-pattern.md` — rule-now / shim-at-N=2, stated.
- `syscall-doctrine.md` — the shim extraction itself.
- `no-guardrail-evasion.md` — rule + mechanical gate (matured).
- `verify-canonical-not-claimed.md` — full traversal this session.

## Falsification

Wrong if, over the next ~10 lessons it governs: rules that genuinely
recur 2× independently fail to ever earn a shim (the gate is a dead
end, not a ladder); OR shims ship at N<2 independent and churn (the
N-gate is not actually enforced); OR a stage advances while *dropping*
its falsification clause (the invariant is not preserved → the functor
claim is false); OR artefacts promoted at N=2 are reverted or corrected
within 30 days more often than artefacts promoted under N=3 were (two is
too low -- go back to three). Track: ratio of matured (shimmed) rules whose
shim carries a falsifier; premature-shim revert rate, against the N=3
baseline recovered from git history.

## Plain language

Lessons earn stronger enforcement by surviving more independent proof:
a one-off becomes a written rule fast (cheap to undo) but unbreakable
machinery only after proving itself two separate times, with its first
build checked by a person, then tested and watched — the proof
condition travels with it the whole way.
