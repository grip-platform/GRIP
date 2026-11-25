---
name: meter
description: Cross-harness usage fuel-gauge — see how much AI you burned across every coding CLI (claude/codex/opencode/amp/…) as a fuel gauge, cost sparkline, harness split, model mix, cache savings and session heatmap. Wraps ccusage with a deterministic delight renderer. Use for "/meter", "how much have I spent", "usage report", "token burn", "which harness am I using".
effort: low
status: Active
version: 1.0.0
triggers:
  - "/meter"
  - "usage report"
  - "token burn"
  - "how much have I spent"
  - "fuel gauge"
  - "usage dashboard"
integrates:
  - ccusage (external CLI)
  - lib/meter.py (deterministic renderer)
see_also:
  - llm-usage
  - aurora
  - grip-haiku
---

# GRIP /meter

Your cross-harness usage fuel-gauge. `ccusage` already detects and aggregates
token/cost across **every coding CLI** — claude, codex, opencode, amp, droid,
codebuff, hermes, pi, goose. `/meter` wraps it with richer detail and a
delightful, deterministic render — no LLM, so the numbers are never invented.

## Run

```bash
python3 -m lib.meter                  # 30-day fuel gauge + all six views
python3 -m lib.meter --days 7         # last 7 days
python3 -m lib.meter --budget 1500    # drive the gauge % off a monthly budget
GRIP_METER_BUDGET=1500 python3 -m lib.meter   # budget via env (persistent)
```

Under the hood it shells out to `ccusage --json`, so it inherits ccusage's
harness detection with zero per-harness config. Keep ccusage current:

```bash
npm install -g ccusage@latest
```

## The six views

1. **FUEL** — total cost as a fuel tank (green < 60% of budget, yellow < 85%,
   red beyond). Honest: if you don't set `GRIP_METER_BUDGET` it shows the raw
   total instead of a made-up percentage.
2. **TREND** — a cost sparkline (`▁▂▃▅▆▇█`) over the window with the peak day.
3. **HARNESS** — claude vs codex vs opencode vs … as a bar chart, so you can
   see which tool is eating the budget.
4. **MODELS** — the top 5 models by cost (usually opus/fable at the top).
5. **CACHE** — tokens read vs written; the cache-carried token count is the
   feel-good "the cache did the heavy lifting" number.
6. **SESSIONS** — total session count + a per-day sparkline (when you work).

## Delight++ ideation (novel, deterministic)

The fuel gauge is the anchor. Two flourish ideas to add when they prove useful:

- **`/meter --burn`** — a dollars-per-hour burn-rate line ("you're burning
  $X/hour today"), so a long session reads as a live meter, not a static report.
- **`/meter --diff <date>`** — "vs last week/month" delta, so you see the
  trend, not just the total (green ↓ = spending less, red ↑ = spending more).

Both are one-line additions to `lib/meter.py`; do NOT add them until the base
six views are actually used (YAGNI — the gauge already carries the signal).

## Harness-agnostic by construction

The skill body above is a protocol — any harness reads it and runs
`python3 -m lib.meter` (or the raw `ccusage` commands). The renderer is pure
stdlib, so it runs identically under Claude Code, Codex, OpenCode, Amp, or a
plain shell. The only dependency is `ccusage` on PATH.

## Falsification

Wrong if: the fuel gauge never changes a behaviour (spending doesn't drop after
seeing it — it's decoration, fold into `/llm-usage`), OR `ccusage`'s JSON schema
drifts and `lib/meter.py` silently mis-parses (add a schema-guard, never a
silent zero).
