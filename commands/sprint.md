---
description: Wrapper around /auto wave-mode that defers per-wave shipping and produces a single delivery PR at sprint end.
disable-model-invocation: false
---

# /sprint — Single-Delivery RSI Sprint

Wrapper that runs an N-hour Fibonacci-wave RSI sprint and ships the
accumulated work as ONE PR at the end. Composition: `/auto wave-mode --no-ship`
followed by a single `/auto` (ship-uncommitted shape).

## Usage

```bash
/sprint                               # 3h default, single PR at end
/sprint 5h "rewrite auth"             # 5h sprint, one PR
/sprint 8h --depth 11 "formal proofs" # Long sprint, one PR
```

## Implementation

Load and execute the sprint skill:

```
Skill(skill="sprint")
```

## What It Does

Runs `/auto wave-mode --no-ship` (deferring per-wave PRs), then runs the
ship-uncommitted shape of `/auto` once at the end on the accumulated
changes. Result: one sprint = one delivery PR. See
`skills/sprint/SKILL.md` for the full contract and falsifier.

## Related

- `/auto wave-mode` — the underlying sprint engine (per-wave shipping by default)
- `/auto deadline=Nh` — autonomous-loop shape (legacy `/auto-rsi`)
- `/auto` (bare) — ship-uncommitted shape (legacy `/auto-ship`)
- `/rsi-optimal`, `/auto-rsi`, `/auto-ship` — all redirect to `/auto`
- `/ultrado` — MYTHIC-tier recursive falsification engine (different scope)
- `/sprint-loop` — autonomous sprint on GitHub issues (different skill)
