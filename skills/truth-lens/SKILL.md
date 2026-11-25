---
name: truth-lens
description: Render a mechanistic probe's workspace-vocabulary readout at a decision point as an honest terminal delight card — what the model's workspace said vs what it typed, agreement signal, and the probe's own verdict. No LLM, no network; reads a probe result JSON. The delight is the provability made visible.
version: 1.0.0
status: Active
triggers:
  - "/truth-lens"
  - "show the workspace readout"
  - "what did the model's workspace say"
  - "render the confabulation probe"
---

# /truth-lens — Workspace Readout Card

One command. What the model's internal workspace said at a decision point,
next to what the model actually typed — rendered honestly, including
negative and blocked results.

## Usage

```bash
bin/truth-lens                     # default: research/jspace-gridlm-probe/result-mps-gpt2.json
bin/truth-lens --input FILE       # any probe/jlens result JSON
bin/truth-lens --json             # machine-readable card data (tests, pipelines)
NO_COLOR=1 bin/truth-lens         # plain text
```

## What It Shows

- **workspace says** — the pooled J-space concept matches at the probed
  decision token (the vocabulary the workspace lens reads out).
- **model types** — the model's actual top-10 output tokens at that point.
- **agreement** — the divergence signal between the two, when the result
  carries one (`DELTA_METRIC` / `delta`).
- **verdict** — the probe's own call, verbatim. A NEGATIVE or BLOCKED
  verdict renders as prominently as a positive one; that is the point.

## How To Read It (the honesty framing)

This card is an **offline research signal from an open-weights probe — a
hypothesis generator, not a live confabulation gate**. That footer is
structural: it renders on every card, and the render test fails if it is
removed. J-lens caveats apply in full (many false positives, mixed
replication at small scale, open weights only — Claude itself is closed and
cannot be probed this way). A gpt2-scale signal must never be laundered
into a scale-up or product decision on its own.

## Implementation

| File | Purpose |
|------|---------|
| `bin/truth-lens` | Stdlib-only renderer — `build_card()` + ANSI card |
| `tests/test_truth_lens_render.py` | Render contract tests incl. the always-on footer |
| `research/jspace-gridlm-probe/` | The probe producing the result JSONs |

## When To Use

After any jspace/jlens probe run, to see the workspace-vs-output picture in
one glance; or pointed at an older result JSON to revisit what the probe
actually found (the default result carries an honest partially-negative
verdict — render it and read the verdict line).
