# Dogfood proof: prompt-cache-keepalive keeps a real Anthropic prefix warm

**Date:** 2026-05-30 · **Verdict:** CONFIRMED · **Hypothesis:** H-CACHE-DOGFOOD
**Harness:** `scripts/dogfood-prompt-cache-keepalive.py` · **Record:** `data/prompt-cache-dogfood.jsonl`

## What this proves (plain English)

Anthropic's prompt cache holds your big conversation prefix so later turns cost ~0.10×
instead of full price. But it expires after ~5 minutes of idle. Step away for a coffee,
come back, and the next turn re-processes the **entire** prefix at the 1.25× cache-write
price — "massive context waste". `prompt-cache-keepalive` (OSS, `oss/prompt-cache-keepalive/`)
sends a tiny "touch" request inside the window so the prefix stays warm and the resume is
cheap. This is the dogfood that proves it works on the **real** Anthropic API — not asserted,
measured.

## The experiment (real Anthropic API, A/B)

Two distinct ~16k-token cached prefixes (distinct => distinct server-side cache entries, so
one arm's touches cannot warm the other). Both idle 420 s (> the ~300 s TTL). Arm A gets no
keepalive (control); arm B is touched by the real `PromptCacheKeepalive` (default config,
interval 270 s) via GRIP's `grip_cache_keepalive.build_cache_touch` (dependency-injected real
urllib transport + cache_control marker — exercises both the published package and the GRIP shim).

| Stage | Arm A — baseline (no keepalive) | Arm B — keepalive |
|---|---|---|
| Write (t=0) | cache_creation 16,028 | cache_creation 16,026 |
| Warm check (t=8s) | cache_read 16,028 | cache_read 16,026 |
| During 420s idle | (untouched) | 1 touch at t=270s → **cache_read 16,026** |
| **Resume (t=435s)** | **cache_creation 16,028, cache_read 0** | **cache_read 16,026, cache_creation 3** |

**Baseline resume RE-WROTE the entire prefix** (the penalty is real and full). **Keepalive
resume was a warm read** — one sub-TTL touch prevented the re-write. The `cache_creation=3` on
arm B is the trivial differing message tail, not a re-write (this magnitude distinction was a
verdict bug the `--quick` smoke run caught and fixed before the real run).

## Why this is a sound proof (exogenous anchor)

The measurement is Anthropic's own `cache_creation_input_tokens` / `cache_read_input_tokens`
usage fields — the provider's billing accounting, which the model cannot control or fake. This
is not self-scoring (`exogenous-anchor-law.md`): the criterion is external to the producer.

**Falsification (pre-registered):** falsified if the keepalive arm re-wrote on resume
(`cache_creation ≈ prefix`) OR the baseline stayed warm (`cache_creation ≈ 0`, no penalty at
this idle/TTL). Neither occurred → CONFIRMED. The `--quick` 60s run correctly reported
"baseline stayed warm" (60s < real TTL) — the harness honestly detects the no-penalty regime,
so a CONFIRMED at 420s is not a rigged test.

## What it saves (cost model, `prompt_cache_keepalive.cost_model`)

Per one 9-minute coffee-break idle that resumes (default config), in base-input-token-equivalents:

| Cached prefix | Re-write (1.25×) | Warm read (0.10×) | Net saved / idle | Breakeven resume-prob |
|---|---|---|---|---|
| 16,028 (this dogfood) | 20,035 | 1,603 | **+15,227** | 0.174 |
| 88,000 (README) | 110,000 | 8,800 | **+83,600** | 0.174 |
| 150,000 (large session) | 187,500 | 15,000 | **+142,500** | 0.174 |
| 200,000 | 250,000 | 20,000 | **+190,000** | 0.174 |

Keepalive wins whenever the session is **>17.4% likely to resume**. A heavy GRIP session
(this one's cached prefix is 100k+ tokens of CLAUDE.md + rules + tools) saves ~140k+
token-equivalents per resumed coffee-break idle.

## Honest boundary (R0)

This dogfood proves the technology on the real Anthropic API and projects the saving to a
session of this size. It does **not** claim to have kept *this specific Claude Code session's*
cache warm — the CC harness owns its own request loop and cache breakpoints, which a tool call
cannot inject into. The README's own honest limitation applies: keepalive works *within* the
provider mechanism; it cannot extend the TTL by API.

## The actionable GRIP application

For an **autonomous agent / harness you control** (RSI loop, `/loop`, `ScheduleWakeup`), the
same idea is a **sub-TTL wake**: pace the next iteration `< KeepaliveConfig.interval()` (270 s)
so the loop re-uses the cached prefix and never crosses eviction. Concretely:

- An RSI/converge loop waiting on a background agent (as this session did while polling PR
  merges) should either keep-alive the prefix or wake sub-270s — otherwise each long idle pays
  a full re-write on resume.
- `grip_cache_keepalive.keep_hal_cache_warm(...)` is the ready shim for HAL Anthropic paths.

## Reproduce

```bash
python3 scripts/dogfood-prompt-cache-keepalive.py --quick   # 60s mechanism smoke (expect baseline-warm)
python3 scripts/dogfood-prompt-cache-keepalive.py 420        # full A/B proof (~7.5 min)
```
