# HAL Model Catalog — Verified Snapshot June 2026

Plain-language reference for the June 2026 catalog refresh. Source:
adversarially-verified research workflow (two-pass: research → independent verify).
Only models with `verified=true` are listed. See `config/model_catalog.yaml` for
the full machine-readable schema and `config/providers.yaml` for routing config.

---

## What changed in this refresh

**Critical routing fix:** `routing.default_model` and `fallback_chain` both pointed
at `groq/llama-3.3-70b-versatile`, which Groq deprecated on 2026-06-17 along with
all Llama/Qwen/DeepSeek routes. Without this fix, the default HAL route returns 404.
Updated to `groq/openai/gpt-oss-120b`.

**Suspended models (DO NOT USE):**
- `claude-fable-5` (claude-opus-5-20260612) — US export-control restriction, taken
  down by Anthropic 2026-06-12. Do not route, recommend, or add to any config.
- `claude-mythos-5` (claude-sonnet-5-20260612) — same restriction.

**Hard sunset deadline:** `deepseek-chat` and `deepseek-reasoner` (DeepSeek legacy
aliases) will **hard-fail after 2026-07-24 15:59 UTC**. Any pipeline still routing
to these IDs will break. Migrate to `deepseek-v4-flash`.

---

## Provider-by-provider summary

### Anthropic

Current top tier is **Opus 4.8 → Sonnet 4.6 → Haiku 4.5** (Fable 5 suspended).

| Model | Tier | Context | Notes |
|-------|------|---------|-------|
| claude-opus-4-8 | subscription | 1M | Current flagship. Adaptive thinking. |
| claude-sonnet-4-6 | subscription | 1M | Recommended default. 90% prompt-cache discount. |
| claude-haiku-4-5-20251001 | subscription | 200k | Fastest/cheapest. Extended thinking (not adaptive). 64k output cap. |

**Sunset:** `claude-opus-4-7` removed (superseded by 4-8 on flat-fee subscription).
**Suspended:** `claude-fable-5`, `claude-mythos-5` — export-control, do not use.

---

### OpenAI

GPT-5 family replaces the GPT-4o / o-series lineup.

| Model | Tier | Context | Price (in/out per Mtok) | Notes |
|-------|------|---------|------------------------|-------|
| gpt-5.5 | frontier | 1.05M | — | Flagship. 90% cache discount. |
| gpt-5.5-pro | frontier | 1.05M | — | Max reasoning time. High-stakes only. |
| gpt-5.4 | frontier | 128k | — | GPT-4o replacement. Lower cost than 5.5. |
| gpt-5.4-mini | mid | 270k | $0.75/$4.50 | Best value mid-tier. Beats Haiku 4.5 on cost. |
| gpt-5.4-nano | cheap | 400k | — | Ultra-budget floor. |
| gpt-4o | mid (legacy) | 128k | — | Kept for compatibility only. |

**Sunset:** `gpt-4o-mini`, `o3-mini` removed (superseded by gpt-5.4-mini/nano).

---

### Google Gemini

| Model | Tier | Context | Notes |
|-------|------|---------|-------|
| gemini-3.5-flash | premium | 1M | Beats 3.1-pro on Terminal-Bench coding. Natively multimodal. |
| gemini-3.1-pro-preview | frontier | 1M | MMMU-Pro leader. Deep research. (context was wrongly 2M — fixed to 1M) |
| gemini-3.1-flash-lite | cheap | 1M | Cheapest Gemini-3. Multimodal. |
| gemini-2.5-pro | premium | 1M | Stable predecessor. Good fallback. |
| gemini-2.5-flash | cheap | 1M | Proven in GRIP fallback chain. $0.30/$2.50. |
| gemini-2.5-flash-lite | cheap | 1M | Floor. $0.10/$0.40. |

**Sunset:** `gemma-4-27b` (deprecated), `gemini-3-flash-preview` (shutdown).

---

### Moonshot Kimi

Original `moonshot-v1-*` series retired 2026-05-25. Full K2 generation now live.

| Model | Tier | Context | Notes |
|-------|------|---------|-------|
| kimi-k2.7-code | premium | 256k | Strongest open-weight coder (SWE-bench ~Opus-4.8). Always-thinking mandatory. |
| kimi-k2.6 | mid | 256k | Beats GPT-5.4 on SWE-Bench Pro. 300-subagent swarms. Open weight. |
| kimi-k2.5 | mid | 256k | Multimodal (vision). $0.60/$2.50. Cheaper-and-close keep. |

All models are China-hosted (data residency consideration). K2.6/K2.7 are text-only;
K2.5 adds vision.

---

### Alibaba Qwen

| Model | Tier | Context | Notes |
|-------|------|---------|-------|
| qwen3.7-max | premium | 1M | Leads SWE-Bench Pro + Terminal-Bench. 200 tok/s. Text-only, API-only. |
| qwen3.7-plus | mid | 1M | Multimodal (text/image/video). ~6x cheaper than Max. |
| qwen3.6-35b-a3b | cheap | 256k native / 1M YaRN | Open-weight MoE (35B/3B active). Apache-2.0. 32GB+ VRAM. |
| qwen3.5-flash | cheap | 1M | Budget floor. $0.10/$0.40. |

**Excluded:** `qwen3.6-27b` (pricing unverifiable), `qwen3.6-plus-preview` (superseded).

---

### DeepSeek

| Model | Tier | Context | Notes |
|-------|------|---------|-------|
| deepseek-v4-pro | frontier | 1M | Leads open models on math/STEM/coding. 384k output. 75% price cut. |
| deepseek-v4-flash | mid | 1M | Near-Pro at $0.14/$0.28. 2500 concurrency. |
| deepseek-v3.2 | mid (legacy) | 65k | Kept for compatibility during V4 ramp. |
| deepseek-r1 | mid (legacy) | 65k | Legacy reasoning. Migrate to v4-flash. |

**Hard deadline:** `deepseek-chat` and `deepseek-reasoner` aliases **hard-fail
2026-07-24 15:59 UTC**. Already removed from all routing chains in this refresh.

---

### Meta Llama (via OpenRouter)

Groq deprecated all Llama routes 2026-06-17. Use OpenRouter routes only.

| Model | Tier | Context | Notes |
|-------|------|---------|-------|
| meta-llama/llama-4-scout-17b-16e-instruct | cheap | 131k | Open-weight multimodal MoE. 200+ languages. OpenRouter only. |
| meta-llama/llama-4-maverick-17b-128e-instruct | cheap | 131k | Multimodal open-weight. OpenRouter only (Groq deprecated 2026-03-09). |
| meta/llama-3.3-70b | cheap | 131k | Stable Llama 3.3. Cheaper-and-close keep. |

---

### xAI Grok

| Model | Tier | Context | Notes |
|-------|------|---------|-------|
| grok-4.3 | frontier | 1M | Lowest hallucination rate. Configurable reasoning depth. AWS Bedrock. |
| grok-build-0.1 | cheap | 256k | Fastest xAI (100+ tok/s). Native MCP. Coding/agentic only. |

**Sunset:** `grok-3`, `grok-3-mini`, `grok-4-0709`, `grok-4-fast-non-reasoning`,
`grok-code-fast-1` (all superseded by 4.3 / Build 0.1).
**Excluded:** `grok-4.20-*` (verified=false — pricing conflict), `grok-5` (unreleased).

---

### Mistral

| Model | Tier | Context | Open weight | Notes |
|-------|------|---------|-------------|-------|
| mistral-medium-3.5 | premium | 256k | No | 77.6% SWE-bench. Dense 128B. Vision. |
| mistral-large-3 | mid | 256k | Apache-2.0 | MoE 675B/41B active. 80-90% cheaper than Sonnet. EU residency. |
| mistral-small-2603 | cheap | 256k | Apache-2.0 | Multimodal 6.5B active. 5-7x cheaper than gpt-5.4-mini. |
| ministral-3b-2512 | cheap | 256k | — | Edge. $0.10/Mtok symmetric. |
| ministral-8b-2512 | cheap | 256k | — | Mid-edge. $0.15/Mtok symmetric. |
| codestral-latest | mid | 256k | — | Coding specialist. FIM. 80+ languages. |
| magistral-medium-latest | premium | 256k | — | Reasoning-focused. Long chain-of-thought. |

---

### Groq (inference infrastructure)

All Llama/Qwen/DeepSeek routes deprecated 2026-06-17. Two models remain.

| Model | Tier | Speed | Price | Notes |
|-------|------|-------|-------|-------|
| openai/gpt-oss-120b | cheap | 500 tok/s | — | Largest current Groq model. Default replacement for llama-3.3-70b. |
| openai/gpt-oss-20b | cheap | 1000 tok/s | $0.075/$0.30 | Fastest. Web-search + code-exec built in. |

**This change breaks the default route.** The `routing.default_model` and all
`fallback_chain`/`tier_chains`/`council` entries referencing `groq/llama-3.3-70b-versatile`
have been repointed to `groq/openai/gpt-oss-120b` in this refresh.

---

### OpenRouter (aggregator)

| Model | Tier | Free? | Notes |
|-------|------|-------|-------|
| deepseek/deepseek-v4-flash | cheap | No | Cheapest V4-Flash passthrough ($0.09/$0.18). |
| qwen/qwen3-coder | cheap | Yes | Free Qwen3 coding specialist. 1M context. |
| nvidia/nemotron-3-super-120b | cheap | Yes | Free 120B. 1M context. |
| anthropic/claude-opus-4.8 | frontier | No | Opus 4.8 without a direct Anthropic key. |
| anthropic/claude-sonnet-4.6 | premium | No | Sonnet 4.6 passthrough. |
| meta-llama/llama-4-scout | cheap | No | Multimodal open-weight. Groq route gone. |
| openrouter/free | cheap | Yes | Auto-routes across 26+ free models. |

---

## Routing changes summary

| Chain | Was | Now |
|-------|-----|-----|
| default_model | groq/llama-3.3-70b-versatile | groq/openai/gpt-oss-120b |
| fallback_chain[0] | groq/llama-3.3-70b-versatile | groq/openai/gpt-oss-120b |
| fallback_chain[2] | xai/grok-3 | xai/grok-4.3 |
| fallback_chain[3] | openrouter/google/gemma-4-31b-it | openrouter/deepseek/deepseek-v4-flash |
| fallback_chain[5] | deepseek/deepseek-chat | deepseek/deepseek-v4-flash |
| quality_cost.simple | groq/llama-3.3-70b-versatile | groq/openai/gpt-oss-120b |
| quality_cost.standard | openrouter/google/gemma-4-31b-it | openrouter/deepseek/deepseek-v4-flash |
| tier_chains.cheap[2] | groq/llama-3.3-70b-versatile | openrouter/deepseek/deepseek-v4-flash |
| tier_chains.premium[0] | xai/grok-3 | xai/grok-4.3 |
| tier_chains.premium[3] | groq/qwen3-32b | moonshot/kimi-k2.7-code |
| council[0] | groq/llama-3.3-70b-versatile | groq/openai/gpt-oss-120b |
| council[3] | xai/grok-3 | qwen/qwen3.7-max |
| council[4] | qwen/qwen3.5-flash | deepseek/deepseek-v4-flash |
| council[5] | deepseek/deepseek-chat | mistral/mistral-large-3 |

---

## Model counts

| Provider | Added | Sunset/removed | Net |
|----------|-------|----------------|-----|
| Anthropic | 2 (opus-4-8, haiku dated) | 1 (opus-4-7) | +1 |
| OpenAI | 5 (5.5/5.5-pro/5.4/5.4-mini/5.4-nano) | 2 (4o-mini, o3-mini) | +3 |
| Gemini | 3 (3.5-flash/3.1-flash-lite/2.5-flash-lite) | 2 (gemma-4-27b, 3-flash-preview) | +1 |
| Moonshot | 3 (k2.7-code/k2.6/k2.5) | 7 (all moonshot-v1-*) | -4 |
| Qwen | 4 (3.7-max/3.7-plus/3.6-35b-a3b/3.5-flash) | 2 (3.5-plus/3.6-plus-preview) | +2 |
| DeepSeek | 2 (v4-pro/v4-flash) | 2 (deepseek-chat/reasoner) | 0 |
| Meta Llama | 3 (scout/maverick/llama-3.3) | 0 | +3 |
| xAI | 2 (grok-4.3/build-0.1) | 5 (grok-3/3-mini/4-0709/4-fast/code-fast) | -3 |
| Mistral | 7 (full 2026 lineup) | 1 (mistral-small-latest) | +6 |
| Groq | 2 (gpt-oss-120b/20b) | 4 (llama-3.3/llama-4-scout/deepseek-r1-distill/qwen3-32b) | -2 |
| OpenRouter | 11 routes | 2 (gemma-4-31b/26b) | +9 |

Total: **+16 models added**, **28 sunset/removed**, catalog now covers verified 2026 lineup.
