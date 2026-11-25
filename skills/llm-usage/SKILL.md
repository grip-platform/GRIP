---
name: llm-usage
description: One-glance dashboard of GRIP's REAL multi-provider LLM usage (groq/gemini/qwen/deepseek/xai/anthropic via HAL), with IDR-style /verify provenance — a tamper-evident signed chain proving the per-provider counts. Use when asked "which models/providers is GRIP actually using", to see the HAL fan-out the Claude Code usage panel cannot show, or to verify usage numbers have not been tampered with.
---

# /llm-usage — provider/model usage, provenance-verified

## Why this exists

The Claude Code usage panel measures only the **CC harness** (Opus-direct) — so
it reads ~99% Opus. GRIP's real variety lives in the **HAL fan-out**: `grip_infer`
cascades across groq / gemini / qwen / deepseek / xai / anthropic
(`config/providers.yaml` ladder). That fan-out never flowed through the CC panel,
so it was invisible — declared in config, proven in `~/.hal/state/hal/limits.json`,
but never *counted*. This dashboard counts it.

## Provenance (IDR-style)

Every LLM call is appended to a **signed hash-chain** via `lib/audit_chain`
(HMAC-SHA256 — the same primitive the IDR forest composes). The counts are
**tamper-evident**: edit, reorder, or delete any record and `/verify` flips to
BROKEN. A usage number you cannot falsify is telemetry, not proof
(`rules/exogenous-anchor-law.md`).

## Commands

```bash
grip-llm-usage              # one-glance dashboard (per provider + per model)
grip-llm-usage verify       # IDR-style chain verdict (exit 1 = BROKEN)
```

Equivalent: `python3 -m lib.llm_usage_ledger [dashboard|verify]`.

## What it shows

- **By provider / by model** — calls, share %, audited count, cost$ (where the
  provider reports tokens), last-seen.
- **Chain status** — VERIFIED / BROKEN, with the break location on tamper.

## Where the data comes from

Wired at GRIP's canonical AI syscall (`lib/hal_llm_adapter.py`): the **primary**
rung (`grip_infer` → `_record_grip_infer_usage`) and the **legacy** rung
(direct-provider `_query_direct`) each call `record_usage` exactly once.
Fail-open by construction — a telemetry error never breaks an inference.

- Chain file: `state/llm-usage/usage-chain.jsonl` (override `GRIP_LLM_USAGE_CHAIN`).
- Cost via `lib/hal/cost_tracker.calculate_cost` (pricing not duplicated).
- Tests: `tests/test_llm_usage_ledger.py` (Goodhart-proof: tamper/reorder/delete → BROKEN).
