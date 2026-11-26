"""HAL Cost Tracker — token-to-USD calculation for any provider.

Pricing data sourced from CC's modelCost.ts (TF++ analysis) and extended
with Groq, Gemini, OpenRouter, and local model costs.

Prompt-length tiers (Haiku 5.5 over 100,000 prompt tokens, Gemini 3.1 Pro
over 200,000) are applied by ``calculate_cost`` and ``calculate_cost_from_usage``,
the public entry points: a whole request bills at the tier its prompt (input
plus cached tokens) reaches, read from ``_REQUEST_TIERS``. ``_resolve_pricing``
returns a model's BASE row only, so a caller that needs a request's real rate
goes through ``calculate_cost``, never the base row (AGORA on GRIP#8213).

Usage:
    from lib.hal.cost_tracker import CostTracker

    tracker = CostTracker()
    cost = tracker.calculate("claude-opus-4-7", input_tokens=1000, output_tokens=500)
    tracker.record("claude-opus-4-7", input_tokens=1000, output_tokens=500)
    print(tracker.total_usd)
"""
import logging
import os
import re
from datetime import datetime, timezone
import threading

from lib.hal.types import CostSnapshot, TokenUsage

# Unpriced-model ids already warned about — one warning per id, not per call
# (AGORA #5179: _resolve_pricing runs on every token-count).
_UNPRICED_WARNED: set[str] = set()

# Pricing per million tokens (USD) — sourced from CC modelCost.ts + provider docs
# Format: (input_per_mtok, output_per_mtok, cache_read_per_mtok, cache_write_per_mtok)
_PRICING: dict[str, tuple[float, float, float, float]] = {
    # Anthropic (from TF++ src/utils/modelCost.ts)
    # Fable 5 / Mythos 5 verified live against the published model-pricing table
    # at platform.claude.com/docs/en/about-claude/pricing 2026-08-25: "$10 / MTok"
    # base input, "$12.50 / MTok" 5m cache writes, "$1 / MTok" cache hits &
    # refreshes, "$50 / MTok" output — identical rows for both models. The
    # cache_write slot carries the 5m rate, matching every other row here; the
    # 1h rate ($20 / MTok) has no slot in this 4-tuple.
    # Without these rows both fell through to _DEFAULT_PRICING (Sonnet tier),
    # under-billing Fable — the current default model — 3.33x on input and
    # output alike, which also under-counts enforce_session_budget.
    # Mythos 5 is limited-availability and not callable on this machine's
    # accounts; its row is docs-verified defensive truth, not an inference.
    # Fast mode is NOT offered for either (Opus 5 / Opus 4.8 only, per the same
    # page), so there is deliberately no "-fast" row for them.
    #
    # The 5.1 generation (2026-09-01) keeps input and output at $10 / $50 but
    # cuts CACHE READS from $1 to $0.25 per MTok — the same published table now
    # reads "$0.25 / MTok" for Fable 5.1 / Mythos 5.1 under "Cache Hits &
    # Refreshes", footnoted: "Cache hits and refreshes on Claude Fable 5.1 and
    # Claude Mythos 5.1 are priced at 0.025x the base input price. All other
    # models use the standard 0.1x multiplier." Re-read live 2026-09-01.
    #
    # These rows are not cosmetic. _resolve_pricing falls back to the LONGEST
    # matching prefix, and "claude-fable-5-1".startswith("claude-fable-5") is
    # true — so without a 5.1 row every 5.1 call would silently bill cache
    # reads at the 5.0 rate, 4x the real price. Cache reads dominate the token
    # mix on long agentic sessions, which is precisely where 5.1 is aimed.
    # Fable 5.2 is priced BELOW Fable 5.1 on every column. The published table, read
    # live 2026-11-11, reads "Claude Fable 5.2 | $8 / MTok | $10 / MTok | $16 / MTok |
    # $0.20 / MTok | $40 / MTok" (input, 5m cache write, 1h cache write, cache hits,
    # output). Without this row the point-release rule refuses to bill it as Fable 5,
    # so every 5.2 call would be logged as unpriced instead of costed.
    "claude-fable-5-2":     (8.0,   40.0,  0.20,  10.0),
    "claude-fable-5-1":     (10.0,  50.0,  0.25,  12.5),
    "claude-mythos-5-1":    (10.0,  50.0,  0.25,  12.5),
    "claude-fable-5":       (10.0,  50.0,  1.0,   12.5),
    "claude-mythos-5":      (10.0,  50.0,  1.0,   12.5),
    # Opus 5.5 (2026-09-23) is priced BELOW Opus 5. The published table reads
    # "Claude Opus 5.5 | $4 / MTok | $5 / MTok | $8 / MTok | $0.20 / MTok |
    # $20 / MTok" (input, 5m cache write, 1h cache write, cache hits, output),
    # footnoted "Cache hits and refreshes on Claude Opus 5.5 are priced at 0.05x
    # the base input price". Re-read live 2026-09-24. Before this row the
    # longest-prefix fallback billed it as claude-opus-5: +25% on input and
    # output, +150% on cache reads.
    "claude-opus-5-5":      (4.0,   20.0,  0.2,   5.0),
    # Opus pricing has held at 5/25 in→out across 4.5/4.6/4.7/4.8/5 (same SKU price;
    # claude-opus-5 verified $5/$25 live platform.claude.com 2026-07-25).
    "claude-opus-5":        (5.0,   25.0,  0.5,   6.25),
    "claude-opus-4-8":      (5.0,   25.0,  0.5,   6.25),
    "claude-opus-4-8-fast": (30.0,  150.0, 3.0,   37.5),
    # Opus 4.7 launched 2026-04-16 at the same price as 4.6 (per Anthropic announcement).
    "claude-opus-4-7":      (5.0,   25.0,  0.5,   6.25),
    "claude-opus-4-7-fast": (30.0,  150.0, 3.0,   37.5),
    "claude-opus-4-6":      (5.0,   25.0,  0.5,   6.25),
    "claude-opus-4-6-fast": (30.0,  150.0, 3.0,   37.5),
    "claude-opus-4-5":      (5.0,   25.0,  0.5,   6.25),
    # Sonnet 5 had no row, so it fell through to _DEFAULT_PRICING, the Sonnet
    # 4.6 tier ($3 / $15). Published 2026-09-24: "Claude Sonnet 5 | $2 / MTok |
    # $2.50 / MTok | $4 / MTok | $0.20 / MTok | $10 / MTok", and footnote 3 says
    # the $2 / $10 introductory price "is now the standard price".
    # Sonnet 5.5 (released 2026-09-28) keeps Sonnet 5's $2 / $10 and halves cache
    # hits. The published table, read 2026-10-12 and again 2026-10-16 (GRIP#8413):
    # "Claude Sonnet 5.5 | $2 / MTok | $2.50 / MTok | $4 / MTok | $0.10 / MTok |
    # $10 / MTok" (input, 5m cache write, 1h cache write, cache hit, output),
    # footnote 2: "Cache hits and refreshes on Claude Opus 5.5 and Claude Sonnet
    # 5.5 are priced at 0.05x the base input price". Its OWN row: the
    # _VERSION_CONTINUATION rule below keeps it from inheriting Sonnet 5, so
    # without one it billed at the loud $3 / $15 default.
    "claude-sonnet-5-5":    (2.0,   10.0,  0.1,   2.5),
    "claude-sonnet-5":      (2.0,   10.0,  0.2,   2.5),
    "claude-sonnet-4-6":    (3.0,   15.0,  0.3,   3.75),
    "claude-sonnet-4-5":    (3.0,   15.0,  0.3,   3.75),
    # Haiku 5.5 is priced by PROMPT LENGTH. The same table, read 2026-10-12:
    # "Claude Haiku 5.5 (for prompts up to 100,000 tokens) | $0.10 / MTok |
    # $0.125 / MTok | $0.20 / MTok | $0.01 / MTok | $0.50 / MTok". This row is
    # that first tier; _REQUEST_TIERS carries the rates for prompts over 100,000
    # tokens, and calculate_cost picks the tier by the request's prompt size.
    "claude-haiku-5-5":     (0.1,   0.5,   0.01,  0.125),
    "claude-haiku-4-5":     (1.0,   5.0,   0.1,   1.25),
    "claude-haiku-3-5":     (0.8,   4.0,   0.08,  1.0),
    # OpenAI
    "gpt-4o":               (2.5,   10.0,  1.25,  2.5),
    "gpt-4o-mini":          (0.15,  0.6,   0.075, 0.15),
    "o1":                   (15.0,  60.0,  7.5,   15.0),
    "o3-mini":              (1.1,   4.4,   0.55,  1.1),
    # GPT-6.2 Astra / Sol -- launched 2026-11-17, onboarded 2026-11-24
    # (dogfood-domain family 8). developers.openai.com/api/docs/pricing, read
    # 2026-11-24, Standard tier, short context (<=272K input). Field order is
    # THIS DICT'S OWN (input, output, cache_read, cache_write-30m) -- NOT the
    # order prose lists them in below: Astra $8 in / $40 out / $0.40 cached /
    # $10 30-minute cache write per 1M; Sol $2 / $10 / $0.10 / $2.50. A
    # 12-hour cache write ($20 Astra, $5 Sol) is not representable in this
    # 4-tuple and is documented on the catalogue row instead. The >272K tier
    # (input+cache 2x, output 1.5x) IS representable and lives in
    # _REQUEST_TIERS below, keyed by this same dict's id.
    "gpt-6.2-astra":        (8.0,   40.0,  0.40,  10.0),
    "gpt-6.2-sol":          (2.0,   10.0,  0.10,  2.50),
    # gpt-6-astra and gpt-5.6-sol/-terra/-luna (GRIP#9444): this file had no row
    # for any of the four, so each fell through to _DEFAULT_PRICING (the
    # Sonnet-tier estimate) silently; HAL's copy of this module already carried
    # them. Re-verified live (not just copied) against the embedded pricing
    # table at developers.openai.com/api/docs/pricing, read 2026-11-24, Standard
    # tier, <=272K input -- the rendered row for each id is [input, cached
    # input, cache write, output]: gpt-6-astra [10, 1, 12.5, 50]; gpt-5.6-sol
    # [4, 0.4, 5, 20]; gpt-5.6-terra [2, 0.2, 2.5, 12]; gpt-5.6-luna
    # [0.2, 0.02, 0.25, 1.2] -- all four match HAL's figures exactly.
    "gpt-6-astra":          (10.0,  50.0,  1.0,   12.5),
    "gpt-5.6-sol":          (4.0,   20.0,  0.4,   5.0),
    "gpt-5.6-terra":        (2.0,   12.0,  0.2,   2.5),
    "gpt-5.6-luna":         (0.2,   1.2,   0.02,  0.25),
    # gpt-5.5, gpt-5.4, gpt-5.4-mini and gpt-5.4-nano: all four are listed in
    # config/providers.yaml's openai `models:` with NO row in this file at all
    # (found auditing that list while fixing GRIP#9444's four above, via a
    # sweep test over every id the provider declares) -- every one of them was
    # billing at _DEFAULT_PRICING, the same silent-estimate bug, just unnamed
    # by either issue. HAL already carries gpt-5.5/5.4/5.4-mini (not
    # gpt-5.4-nano, which is not in HAL's provider list at all); the same live
    # read, same date, confirms HAL's figures for all three and gives
    # gpt-5.4-nano its own. The vendor's rendered row carries only 3 values for
    # a mini/nano tier -- input, cached input, output, no distinct write rate
    # -- so cache_write carries the input rate as an upper bound throughout;
    # gpt-5.5 and gpt-5.4 publish a dash ("-") for cache writes too, the same
    # convention. gpt-5.4-nano's own UI flag reads "deprecated" but the page
    # still prices and serves it, same as gemini-3.5-flash and
    # mistral-medium-3.5 elsewhere in this file.
    "gpt-5.5":              (5.0,   30.0,  0.5,   5.0),
    "gpt-5.4":              (2.5,   15.0,  0.25,  2.5),
    "gpt-5.4-mini":         (0.75,  4.5,   0.075, 0.75),
    "gpt-5.4-nano":         (0.2,   1.25,  0.02,  0.2),
    # Groq (free tier — $0 for most, minimal for paid)
    # llama-3.3-70b-versatile / llama-4-scout / deepseek-r1-distill-llama-70b
    # REMOVED 2026-08-30 — all three retired at Groq (404 model_not_found).
    # qwen3.8-27b groq pricing: no fetchable source yet -- it bills $0 via the
    # unknown fallback until a verified figure is published. (qwen3.6-27b left
    # Groq on 2026-11-24.)
    "openai/gpt-oss-120b":     (0.15, 0.60, 0.0, 0.0),
    # Gemini -- ai.google.dev/gemini-api/docs/pricing, paid tier, read live
    # 2026-10-01, for prompts up to 200K tokens. Output includes thinking tokens
    # ("Output price (including thinking tokens)"). Creating a context cache bills
    # the input rate plus storage per hour, and storage has no slot here, so
    # cache_write carries the input rate. Pro models bill more above 200K tokens
    # ($4 / $18 for 3.1 Pro), which one tuple cannot carry. The free tier is not
    # modelled: Google lists its content as "Used to improve our products: Yes".
    # The old gemini-3.1-pro row ($1.25 / $10) was gemini-2.5-pro's price, and
    # the old gemini-2.5-flash row ($0.15 / $0.60) a retired rate.
    "gemini-3.1-pro":        (2.00,  12.00, 0.20,  2.00),
    # Gemini 4 Argon -- the same page, read 2026-10-18. Introductory rate: "$2.00
    # through November 30, 2026." in, "$10.00 through November 30, 2026." out
    # (thinking included), "$0.10 through November 30, 2026." cached. The doubling
    # "starting December 1, 2026." sits in _PRICE_SCHEDULE below. Caching is
    # implicit only, so cache_write carries the input rate like the rows above.
    # No prompt-length tier: the page lists none for Argon.
    "gemini-4-argon":        (2.00,  10.00, 0.10,  2.00),
    "gemini-3.5-flash-lite": (0.30,  2.50,  0.03,  0.30),
    # gemini-3.5-flash left the pricing page after its 2026-10-08 deprecation (requests
    # are routed to gemini-3.6-flash); the row is the last published rate, kept while
    # the id is accepted. The vendor does not say how a routed request is billed.
    "gemini-3.5-flash":      (1.50,  9.00,  0.15,  1.50),
    # gemini-3.6-flash and gemini-3.8-flash -- the same page, read 2026-10-18: "$0.75
    # through December 31, 2026." in, "$3.75 through December 31, 2026." out (thinking
    # included), "$0.075" cached; both double on 2027-01-01 (_PRICE_SCHEDULE). Storage
    # per hour has no slot, so cache_write carries the input rate. No prompt tier.
    "gemini-3.6-flash":      (0.75,  3.75,  0.075, 0.75),
    "gemini-3.8-flash":      (0.75,  3.75,  0.075, 0.75),
    "gemini-3.1-flash-lite": (0.25,  1.50,  0.025, 0.25),
    "gemini-2.5-pro":        (1.25,  10.00, 0.125, 1.25),
    "gemini-2.5-flash-lite": (0.10,  0.40,  0.01,  0.10),
    "gemini-2.5-flash":      (0.30,  2.50,  0.03,  0.30),
    # Gemini 4 Carbon and Hydrogen -- aistudio.google.com/docs/pricing, read
    # 2026-11-24 (paid/Standard tier). Carbon: introductory "$2.00 through
    # November 30, 2026." in, "$10.00 through November 30, 2026." out (thinking
    # included), "$0.10 through November 30, 2026." cached; the doubling
    # "starting December 1, 2026." sits in _PRICE_SCHEDULE below. Hydrogen's
    # page lists no dated change: "$1.50" in, "$7.50" out, "$0.075" cached,
    # flat. Both implicit-caching only, so cache_write carries the input rate;
    # neither page lists a prompt-length tier.
    "gemini-4-carbon":       (2.00,  10.00, 0.10,  2.00),
    "gemini-4-hydrogen":     (1.50,  7.50,  0.075, 1.50),
    # Gemini 4 Neon -- ai.google.dev/gemini-api/docs/pricing, read 2026-11-26
    # (paid/Standard tier): "$3.00" in, "$15.00" out (thinking included), "$0.15"
    # cached, with no dated change and no prompt-length tier, so no schedule row.
    # Not on the Free tier; implicit caching only, so cache_write carries the
    # input rate like the rows above.
    "gemini-4-neon":         (3.00,  15.00, 0.15,  3.00),
    # DeepSeek -- api-docs.deepseek.com/quick_start/pricing, read live
    # 2026-10-01. These are PEAK rates, so an upper bound: off-peak is half, and
    # peak is 01:00-04:00 and 06:00-10:00 UTC on weekdays. "Use deepseek-flash as
    # the model name. The legacy names deepseek-v4-flash and
    # deepseek-v4-flash-vision-exp are still accepted ... billed at the Flash
    # price", so the legacy row equals deepseek-flash and the vision id inherits
    # it by prefix. cache_write is the cache-miss input rate (no write rate is
    # published). The OpenRouter figures these replace under-billed flash 3.7x
    # and pro 2.7x on input.
    "deepseek-flash":        (0.30,  1.20,  0.006, 0.30),
    "deepseek-v4-flash":     (0.30,  1.20,  0.006, 0.30),
    "deepseek-v4-pro":       (1.32,  3.96,  0.044, 1.32),
    # xAI -- the model data embedded in docs.x.ai/docs/models, read 2026-10-01.
    # Its unit (ten-thousandths of a dollar per 1M tokens) was calibrated against
    # the same page's rendered "$2.00 / 1M tokens" card for grok-4.5. Prompts over
    # 200,000 tokens bill double, in _REQUEST_TIERS (GRIP#9220). Without these rows
    # both fell through to the Sonnet-tier default, $3 / $15.
    "grok-4.5":              (2.00,  6.00,  0.30,  2.00),
    "grok-4.3":              (1.25,  2.50,  0.20,  1.25),
    # grok-4.6, the same embedded data read 2026-11-16 (GRIP#9205): 20000 / 60000 /
    # cached 5000 on its us-east-1 and us-west-2 clusters, so $2.00 in, $6.00 out,
    # $0.50 cached. Its us-central-1 listing is 10% higher, which matches the US
    # regional endpoint: docs.x.ai/docs/key-information/regions says "Token usage on
    # the US endpoint costs 10% more than on the global endpoint". config/providers.yaml
    # sends xai to https://api.x.ai/v1, the global endpoint. Prompts over 200,000
    # tokens bill double (_REQUEST_TIERS).
    "grok-4.6":              (2.00,  6.00,  0.50,  2.00),
    # grok-4.7 -- the model data embedded in docs.x.ai/developers/models/grok-4.7,
    # read 2026-11-24 ("name":"grok-4.7"): promptTextTokenPrice 20000,
    # cachedPromptTokenPrice 5000, completionTextTokenPrice 60000, the same unit
    # as grok-4.5/4.6. The vendor's own release-notes page (same read) confirms
    # it in prose: "Pricing is $2 / $0.50 / $6 per 1M tokens (input / cached
    # input / output) below 200k prompt tokens, and $4 / $1 / $12 above."
    "grok-4.7":                     (2.00,  6.00,  0.50,  2.00),
    # grok-4.20-0309-reasoning and -non-reasoning: the same embedded data, read
    # 2026-11-24 ("name":"grok-4.20-0309-reasoning" / "-non-reasoning"):
    # promptTextTokenPrice 12500, cachedPromptTokenPrice 2000,
    # completionTextTokenPrice 25000 for BOTH -- the same row as grok-4.3.
    # PARITY: GRIP's lib/broly_council.py and lib/council_seat.py already call
    # -non-reasoning; -reasoning is this family's workhorse pick. Without these
    # rows both fell through to the Sonnet-tier default, $3 / $15.
    "grok-4.20-0309-reasoning":     (1.25,  2.50,  0.20,  1.25),
    "grok-4.20-0309-non-reasoning": (1.25,  2.50,  0.20,  1.25),
    # Alibaba DashScope -- alibabacloud.com/help/en/model-studio/model-pricing,
    # international deployment, up to 1M tokens, read 2026-10-01. Without this
    # row the "qwen" local-model fallback in _resolve_pricing billed AGORA's Qwen
    # speaker at $0. No cache rate is published beside it, so both cache slots
    # carry the input rate as an upper bound.
    "qwen3.5-flash":         (0.10,  0.40,  0.10,  0.10),
    # qwen3.7-plus, the same page read 2026-11-16 (GRIP#9205), Singapore table,
    # International scope, 0<Token<=256K: "List price $0.4 (Limited-time 20% off)"
    # in and "List price $1.6 (Limited-time 20% off)" out, thinking or not. The row
    # keeps the list price, an upper bound, because the discount names no end date.
    # The snapshot qwen3.7-plus-2026-05-26 lists the same figures and inherits this
    # row. Its cache rates are on alibabacloud.com/help/en/model-studio/context-cache,
    # read the same day: an implicit hit is "typically billed at 20% of the standard
    # input token price" and an explicit one at 10%, so cache read carries the 20%;
    # explicit cache creation "is billed at 125% of the standard input token price".
    # Prompts over 256K tokens bill $1.2 / $4.8 (_REQUEST_TIERS).
    "qwen3.7-plus":          (0.40,  1.60,  0.08,  0.50),
    # qwen4-max: alibabacloud.com/help/en/model-studio/qwen4-max +
    # .../billing-for-model-studio, both read 2026-11-24, Singapore/
    # International, 0<Token<=1M: "$1.5" in, "$4.5" out. GA 2026-11-16
    # (alibabacloud.com/press-room/alibaba-unveils-qwen4-max), which also
    # states "Input (Implicit Cache Hit) is priced at $0.075 per 1M tokens" --
    # the cache-read slot. No cache-WRITE rate is published, so that slot
    # carries the input rate as an upper bound, as for every other
    # unpublished-write Qwen/Kimi row in this table.
    "qwen4-max":             (1.50,  4.50,  0.075, 1.50),
    # qwen3.8-flash: the same billing page, same date, same region: "$0.15" in,
    # "$0.47" out, one tier up to 1M. No cache figures published for this id;
    # both cache slots carry the input rate as an upper bound.
    "qwen3.8-flash":         (0.15,  0.47,  0.15,  0.15),
    # Moonshot (Kimi) — K3 pricing per platform.kimi.ai/docs/pricing/chat-k3 (verified 2026-07-25)
    "kimi-k3":             (3.00,  15.00, 0.30,  3.00),  # cache_write = input-miss upper bound (no published write rate)
    # Kimi K3.1 Pro and Highspeed: the price table embedded in
    # platform.kimi.ai/docs/pricing/chat, read 2026-11-24. Flat at every context
    # length. cache_write is the 5-minute (default TTL) write rate; the 1-hour
    # write is double it ($4.00 and $8.00).
    "kimi-k3.1-pro":       (2.00,  10.00, 0.20,  2.00),
    "kimi-k3.1-pro-highspeed": (4.00, 20.00, 0.20, 4.00),
    # Mistral Large 4 and Small 2603 (/dogfood-domain, 2026-11-24): both read from
    # the "pricing" array embedded in docs.mistral.ai/inference/pricing's own page
    # data (a search summary is not the docs; this was fetched and the JSON read).
    # Large 4 publishes a "Cached input" row at 0.1x input ($0.07); Small 2603
    # publishes no cache row at all, so both cache slots carry the input rate as
    # an upper bound, the same convention qwen3.5-flash uses above.
    "mistral-large-4":       (0.70,  2.10,  0.07,  0.70),
    "mistral-small-2603":    (0.15,  0.60,  0.15,  0.15),
    # The REST of the paid Mistral ids (GRIP#9420): every one of them fell
    # through to the free "local" rate below, because _resolve_pricing's
    # "mistral" substring check treated the whole family as local unless an id
    # had its own exact row -- these did not. Re-read docs.mistral.ai/inference/
    # pricing's embedded JSON live 2026-11-24 (fetched and parsed, not a search
    # summary; the page's own modelIds group a dated id with its "-latest" alias
    # on ONE pricing card, so each pair below shares a row):
    # - mistral-large-latest shares mistral-large-4's card (modelIds
    #   ["mistral-large-4","mistral-large-4-0","mistral-large-latest"]) -- the
    #   same (0.70, 2.10, 0.07, 0.70) row above.
    # - mistral-medium-3.5 (the card's own modelId is "mistral-medium-3-5", a
    #   dash where config/providers.yaml's id has a dot -- the vendor's URL-safe
    #   spelling, not a different model; its alias is "mistral-medium-latest"):
    #   input $1.5, output $7.5, no cached-input row published.
    # - ministral-8b-2512 (alias ministral-8b-latest): input $0.15, output
    #   $0.15, no cache row.
    # - ministral-3b-2512 (alias ministral-3b-latest): input $0.1, output $0.1,
    #   no cache row.
    # - codestral-latest shares the "codestral-2508" card: input $0.3, output
    #   $0.9, no cache row.
    # Every one of the five above publishes no cached-input price, so both
    # cache slots carry the input rate as an upper bound, same as Small 2603.
    "mistral-large-latest":    (0.70,  2.10,  0.07,  0.70),
    "mistral-medium-3.5":      (1.5,   7.5,   1.5,   1.5),
    "ministral-8b-2512":       (0.15,  0.15,  0.15,  0.15),
    "ministral-3b-2512":       (0.1,   0.1,   0.1,   0.1),
    "codestral-latest":        (0.3,   0.9,   0.3,   0.3),
    # magistral-medium-latest is NOT on the aggregate /inference/pricing page at
    # all (its modelIds array is absent from the page's own pricing-card list,
    # read 2026-11-24) -- its price lives only on the model's own page,
    # docs.mistral.ai/models/magistral-medium (flagship) and
    # .../magistral-medium-1-2-25-09 (the newest dated version, read the same
    # day): both show input $2, output $5, no cached-input row. Reasoning
    # models' output is this dict's one slot regardless of how much of it is
    # thinking versus the visible answer.
    "magistral-medium-latest": (2.0,   5.0,   2.0,   2.0),
    # Kimi lineage per platform.kimi.ai/docs/pricing/chat-k26|chat-k27-code
    # (verified 2026-07-26; -highspeed added 2026-09-07 from the same k27-code
    # page); cache_write = input-miss upper bound for all (Moonshot publishes
    # no distinct write rate). kimi-k2.5 was retired upstream 2026-08-31 and
    # its row removed with it.
    "kimi-k2.6":           (0.95,  4.00,  0.16,  0.95),
    "kimi-k2.7-code":      (0.95,  4.00,  0.19,  0.95),
    "kimi-k2.7-code-highspeed": (1.90, 8.00, 0.38, 1.90),  # ~180-260 tok/s tier
    # Zhipu (GLM): GLM-5.3, docs.z.ai/guides/overview/pricing.md, read 2026-11-24 --
    # "GLM-5.3 | $1.4 | $0.26 | Limited-time Free | $4.4" (input, cached input, cached
    # input storage, output). No distinct cache-write rate is published; the 4th slot
    # carries the (free) storage rate, not a write premium. This is now FAMILY_FLAGSHIP
    # ["glm"]'s model (GRIP#9431: Zhipu answers every glm-5.1 request with GLM-5.3), so
    # without this row every one of those calls would have billed at _DEFAULT_PRICING
    # (this repo carries no bare "glm-5" row to collide with via the longest-prefix
    # fallback -- see tests/test_glm_5_4_onboarding.py -- so the unpriced fallback, not
    # a sibling's row, is what a missing row here would have produced).
    "glm-5.3":             (1.4,   4.4,   0.26,  0.0),
    # GLM-5.3-Flash, same page, same date -- "GLM-5.3-Flash | $0.15 | $0.03 |
    # Limited-time Free | $0.50". GLM-5.4 deliberately has NO row: its Model API is
    # "pending completion of the security assessment" and it is absent from the
    # pricing page, so it falls through to the loud unpriced default rather than a
    # fabricated number.
    "glm-5.3-flash":       (0.15,  0.50,  0.03,  0.0),
    # Local (free)
    "local":               (0.0,   0.0,   0.0,   0.0),
}

# Fallback for unknown models
_DEFAULT_PRICING = (3.0, 15.0, 0.3, 3.75)  # Sonnet-tier

# Rates that rise with the size of a request's prompt. The WHOLE request bills
# at the tier its prompt reaches, so a row lists (threshold, rates) pairs in
# ascending order: a prompt of more than ``threshold`` tokens bills at ``rates``.
# A prompt counts its cached tokens too. That is the vendor's own rule for Haiku
# 5.5 ("A request's prompt length counts all of its input tokens, including
# cache reads and cache writes", read 2026-10-12), and for the other rows it can
# only over-count, which keeps an estimate an upper bound. Ported from HAL's
# lib/hal/cost_tracker.py (HAL#707).
_REQUEST_TIERS: dict[str, tuple[tuple[int, tuple[float, float, float, float]], ...]] = {
    # platform.claude.com/docs/en/about-claude/pricing, read 2026-10-12: "Claude
    # Haiku 5.5 (for prompts over 100,000 tokens) | $0.50 / MTok | $0.625 / MTok |
    # $1 / MTok | $0.05 / MTok | $2.50 / MTok" (input, 5m write, 1h write, cache
    # hit, output); five times the first tier on every slot.
    "claude-haiku-5-5": ((100_000, (0.50, 2.50, 0.05, 0.625)),),
    # ai.google.dev/gemini-api/docs/pricing, read 2026-10-01 (the figure the
    # gemini-3.1-pro comment above could not carry in one tuple): prompts over
    # 200k tokens bill $4.00 in, $18.00 out (thinking included), $0.40 cached.
    "gemini-3.1-pro": ((200_000, (4.00, 18.00, 0.40, 4.00)),),
    # docs.x.ai/docs/models, read 2026-11-16: grok-4.6's long-context fields are
    # 40000 / 120000 / cached 10000 past "longContextThreshold":"200000", so $4.00
    # in, $12.00 out and $1.00 cached, on the same unit as its base row.
    "grok-4.6": ((200_000, (4.00, 12.00, 1.00, 4.00)),),
    # The same data, read 2026-11-16 (GRIP#9220): past the same threshold, grok-4.5
    # lists 40000 / 120000 / cached 6000 and grok-4.3 25000 / 50000 / cached 4000.
    # The cache-write slot carries the input rate, as in their base rows.
    "grok-4.5": ((200_000, (4.00, 12.00, 0.60, 4.00)),),
    "grok-4.3": ((200_000, (2.50, 5.00, 0.40, 2.50)),),
    # grok-4.7: docs.x.ai/developers/models/grok-4.7, read 2026-11-24, embedded data
    # past its "longContextThreshold":"200000": promptTextTokenPriceLongContext
    # 40000, completionTokenPriceLongContext 120000, cachedPromptTokenPriceLongContext
    # 10000 -- the same tier as grok-4.6, confirmed by the vendor's own prose on the
    # same page/date ("$4 / $1 / $12 above" 200k prompt tokens).
    "grok-4.7": ((200_000, (4.00, 12.00, 1.00, 4.00)),),
    # grok-4.20-0309-reasoning and -non-reasoning: the same embedded data, read
    # 2026-11-24: promptTextTokenPriceLongContext 25000, completionTokenPriceLong
    # Context 50000, cachedPromptTokenPriceLongContext 4000 for BOTH -- the same
    # tier as grok-4.3.
    "grok-4.20-0309-reasoning": ((200_000, (2.50, 5.00, 0.40, 2.50)),),
    "grok-4.20-0309-non-reasoning": ((200_000, (2.50, 5.00, 0.40, 2.50)),),
    # alibabacloud.com/help/en/model-studio/model-pricing, read 2026-11-16:
    # qwen3.7-plus, International, "256K<Token<=1M" lists $1.2 in and $4.8 out, and
    # the base row's cache ratios (20% read, 125% creation) apply to the $1.2.
    "qwen3.7-plus": ((256_000, (1.20, 4.80, 0.24, 1.50)),),
    # developers.openai.com/api/docs/pricing, read 2026-11-24: "For requests
    # with more than 272K input tokens, input and cache rates double" and
    # "output costs 1.5 times the short-context rate for the whole request" --
    # quoted verbatim, not inferred. Astra: $8->$16 in, $0.40->$0.80 cached,
    # $10->$20 cache write, $40->$60 out. Sol: $2->$4, $0.10->$0.20,
    # $2.50->$5, $10->$15. The vendor page confirms $16/$0.80/$60 and
    # $4/$0.20/$15 directly; the cache-write figures are the stated 2x applied
    # to the already-quoted short-context write rate (gpt-6-astra has the
    # SAME tier, undocumented here, since it predates this mechanism and this
    # PR does not touch its row).
    "gpt-6.2-astra": ((272_000, (16.0, 60.0, 0.80, 20.0)),),
    "gpt-6.2-sol": ((272_000, (4.0, 15.0, 0.20, 5.0)),),
}
# Rates that change on a CALENDAR DATE rather than by prompt size: ascending
# (ISO date, rates); from that UTC day on, the row bills at ``rates``. Argon is
# the first: ai.google.dev/gemini-api/docs/pricing, read 2026-10-18, "$4.00
# starting December 1, 2026." in, "$20.00 starting December 1, 2026." out
# (thinking included), "$0.20 starting December 1, 2026." cached. Without this the
# tracker would bill the introductory rate for ever, under-counting by half from
# that day. Pinned by tests/test_cost_tracker_vendor_prices.py.
_PRICE_SCHEDULE: dict[str, tuple[tuple[str, tuple[float, float, float, float]], ...]] = {
    "gemini-4-argon": (("2026-12-01", (4.00, 20.00, 0.20, 4.00)),),
    # aistudio.google.com/docs/pricing, read 2026-11-24: "$4.00 starting
    # December 1, 2026." in, "$20.00 starting December 1, 2026." out (thinking
    # included), "$0.20 starting December 1, 2026." cached -- Carbon's rate
    # doubles the same day as Argon's. Hydrogen carries no schedule: its page
    # lists no dated change.
    "gemini-4-carbon": (("2026-12-01", (4.00, 20.00, 0.20, 4.00)),),
    # The same page, read 2026-10-18: "$1.50 starting January 1, 2027." in, "$7.50
    # starting January 1, 2027." out, "$0.15 starting January 1, 2027." cached.
    "gemini-3.6-flash": (("2027-01-01", (1.50, 7.50, 0.15, 1.50)),),
    "gemini-3.8-flash": (("2027-01-01", (1.50, 7.50, 0.15, 1.50)),),
}


def _today() -> str:
    """Today's UTC date as ISO text; the seam tests move the calendar through."""
    return datetime.now(timezone.utc).date().isoformat()


def _scheduled(key: str, rates: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
    """The rates ``key`` bills at today, after every dated change that has arrived."""
    today = _today()
    for effective, dated in _PRICE_SCHEDULE.get(key, ()):
        if today >= effective:
            rates = dated
    return rates


# Opt-in pre-spend session budget cap (R-3). Block BEFORE the call, not
# record after. ZERO-REGRESSION by construction: env UNSET -> no cap, and
# no existing caller invokes precheck() -> behaviour byte-identical to the
# pre-R-3 tracker. Fail-OPEN: any parse error -> no cap (a malformed budget
# never blocks a legitimate call). Per-session ceiling (one CostTracker is
# one session; _total_usd is the session cumulative).
_BUDGET_ENV = "GRIP_HAL_SESSION_BUDGET_USD"


class BudgetExceeded(RuntimeError):
    """Raised by CostTracker.precheck when a projected spend would push the
    session past GRIP_HAL_SESSION_BUDGET_USD. Plain-language message (Rule 17
    / syscall-doctrine): states spent, projected, ceiling, and the remedy."""


def _read_session_budget() -> float | None:
    """Read the opt-in session ceiling from env. Fail-OPEN: unset, empty,
    non-numeric, or non-positive -> None (no cap, byte-identical to pre-R-3).
    Only a clean positive float arms the cap."""
    raw = os.environ.get(_BUDGET_ENV)
    if not raw:
        return None
    try:
        val = float(raw.strip())
    except (TypeError, ValueError):
        return None
    return val if val > 0 else None


def calculate_cost(
    model: str,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_read_tokens: int = 0,
    cache_creation_tokens: int = 0,
) -> float:
    """Calculate USD cost for a single query, at the tier its prompt size reaches."""
    prompt = input_tokens + cache_read_tokens + cache_creation_tokens
    pricing = _request_rates(model, prompt)
    return (
        (input_tokens / 1_000_000) * pricing[0]
        + (output_tokens / 1_000_000) * pricing[1]
        + (cache_read_tokens / 1_000_000) * pricing[2]
        + (cache_creation_tokens / 1_000_000) * pricing[3]
    )


def calculate_cost_from_usage(model: str, usage: TokenUsage) -> float:
    """Calculate USD cost from a TokenUsage object."""
    return calculate_cost(
        model,
        input_tokens=usage.prompt_tokens,
        output_tokens=usage.completion_tokens,
        cache_read_tokens=usage.cache_read_tokens,
        cache_creation_tokens=usage.cache_creation_tokens,
    )


class CostTracker:
    """Accumulates cost across a session. Thread-safe."""

    def __init__(self) -> None:
        self._total_input = 0
        self._total_output = 0
        self._total_cache_read = 0
        self._total_cache_creation = 0
        self._total_usd = 0.0
        self._per_model: dict[str, float] = {}
        self._per_provider: dict[str, float] = {}
        self._lock = threading.Lock()

    def record(
        self,
        model: str,
        provider: str = "",
        input_tokens: int = 0,
        output_tokens: int = 0,
        cache_read_tokens: int = 0,
        cache_creation_tokens: int = 0,
    ) -> float:
        """Record usage and return the cost for this query."""
        cost = calculate_cost(model, input_tokens, output_tokens,
                              cache_read_tokens, cache_creation_tokens)
        with self._lock:
            self._total_input += input_tokens
            self._total_output += output_tokens
            self._total_cache_read += cache_read_tokens
            self._total_cache_creation += cache_creation_tokens
            self._total_usd += cost
            self._per_model[model] = self._per_model.get(model, 0.0) + cost
            if provider:
                self._per_provider[provider] = self._per_provider.get(provider, 0.0) + cost
        return cost

    def record_response(self, model: str, provider: str, usage: TokenUsage) -> float:
        """Record from a ChatResponse's TokenUsage."""
        return self.record(
            model, provider,
            input_tokens=usage.prompt_tokens,
            output_tokens=usage.completion_tokens,
            cache_read_tokens=usage.cache_read_tokens,
            cache_creation_tokens=usage.cache_creation_tokens,
        )

    @property
    def total_usd(self) -> float:
        return self._total_usd

    def snapshot(self) -> CostSnapshot:
        """Return current accumulated cost as immutable snapshot."""
        with self._lock:
            return CostSnapshot(
                input_tokens=self._total_input,
                output_tokens=self._total_output,
                cache_read_tokens=self._total_cache_read,
                cache_creation_tokens=self._total_cache_creation,
                total_usd=self._total_usd,
            )

    def per_model(self) -> dict[str, float]:
        """Cost breakdown by model."""
        with self._lock:
            return dict(self._per_model)

    def per_provider(self) -> dict[str, float]:
        """Cost breakdown by provider."""
        with self._lock:
            return dict(self._per_provider)

    def reset(self) -> None:
        """Reset all counters."""
        with self._lock:
            self._total_input = 0
            self._total_output = 0
            self._total_cache_read = 0
            self._total_cache_creation = 0
            self._total_usd = 0.0
            self._per_model.clear()
            self._per_provider.clear()


# Named so a future addition to this list is a visible, reviewable diff
# against the comment on _looks_like_local_family below it, not a silent
# inline edit (AGORA on GRIP#9472). "qwen" is deliberately NOT one of them:
# an unknown DashScope id such as qwen3.7-max is a paid cloud model, and
# billing it at $0 hid it from every budget. Over-counting at the loud
# default is auditable; under-counting to zero is not. HAL dropped it in
# its PR #512.
_LOCAL_FAMILY_SUBSTRINGS = ("llama3", "mistral", "phi")


def _looks_like_local_family(model: str) -> bool:
    """True for a slash-qualified address naming a free local model family.

    GRIP#9420: this check used to fire on a BARE id too, so every paid
    api.mistral.ai model whose name happens to contain "mistral" --
    "mistral-medium-3.5", "codestral-latest", a ministral snapshot -- billed
    $0 unless it had carried its own exact _PRICING row. api.mistral.ai never
    puts a slash in its OWN model ids (config/providers.yaml's mistral list is
    all bare names); every genuinely LOCAL id these three substrings are meant
    to catch is either the Ollama "name:tag" form _resolve_pricing checks
    separately, or a slash-qualified HF-repo-style address (e.g. an
    mlx-community/* id), and every such address already carries its own
    explicit $0 row in this table. Requiring "/" here keeps a bare paid id off
    this free fallback without narrowing what a slash-qualified local address
    still catches.

    Known residual (not closed here): "mistral/<id>" supplies its OWN "/" and
    its OWN "mistral" substring from the provider prefix alone, so a FUTURE
    Mistral id with no row, called in that provider-qualified address form,
    still reaches this free path -- see
    test_a_provider_qualified_unpriced_mistral_id_is_a_known_residual_gap in
    tests/test_cost_tracker_vendor_prices.py. Closing it needs this module to
    know which address prefixes name a cloud BILLING provider rather than a
    local HF-repo org, which couples this pure pricing module to
    config/providers.yaml.
    """
    return "/" in model and any(local in model.lower() for local in _LOCAL_FAMILY_SUBSTRINGS)


def _resolve_pricing(model: str) -> tuple[float, float, float, float]:
    """Resolve pricing for a model, with fuzzy matching."""
    key = _pricing_key(model)
    if key is not None:
        return _scheduled(key, _PRICING[key])

    # An Ollama "name:tag" id ("qwen3:8b", "llama3:70b") is served locally.
    if ":" in model and "/" not in model:
        return _PRICING["local"]

    if _looks_like_local_family(model):
        return _PRICING["local"]

    # Unpriced model — warn ONCE per model id (AGORA #5179): an unpriced real
    # model is billed at a rate that is not its cost, so it must never be silent.
    if model and model not in _UNPRICED_WARNED:
        _UNPRICED_WARNED.add(model)
        logging.getLogger(__name__).warning(
            "no pricing entry for model %r — billed at the default rate %s "
            "(input, output, cache read, cache write per MTok), which is not its "
            "cost. Add it to lib/hal/cost_tracker._PRICING.", model, _DEFAULT_PRICING)
    return _DEFAULT_PRICING


# A point release is a DIFFERENT model, not a snapshot of its sibling:
# "claude-opus-5-5" starts with "claude-opus-5", yet bills $4 / $20 against Opus
# 5's $5 / $25, and "claude-fable-5-1" cut cache reads 4x against Fable 5. So a
# key may stand in for a longer id only when what follows the key is a snapshot
# date ("-20251001", "-2024-07-18", "-05-20") or a named variant ("-fast",
# "-preview"). A short numeric component ("-5", "-1") continues the VERSION, which
# names another model; such an id falls through to the loud unpriced path instead.
# Measured before shipping: of the 82 model ids config/providers.yaml lists, none
# changes price under this rule.
_VERSION_CONTINUATION = re.compile(r"-(?!\d{2}-\d{2}$)\d{1,2}(?:-|$)")


def _inherits_row(model: str, key: str) -> bool:
    """True when ``key``'s row may price ``model``: a prefix that is not a sibling."""
    return model.startswith(key) and not _VERSION_CONTINUATION.match(model, len(key))


def _pricing_key(model: str) -> str | None:
    """The _PRICING key that prices ``model``, or None when no row may.

    A provider-qualified address ("deepseek/deepseek-v4-flash",
    "groq/openai/gpt-oss-120b") is how AGORA and HAL name a seat, so each
    shorter suffix is tried after the whole id: "groq/openai/gpt-oss-120b"
    reaches the "openai/gpt-oss-120b" row before the bare "gpt-oss-120b".
    Without this every address fell past the table -- deepseek/* to the
    default rate, qwen/* to $0 through the local rule in _resolve_pricing.
    """
    parts = model.split("/")
    for i in range(len(parts)):
        key = _priced_key("/".join(parts[i:]))
        if key is not None:
            return key
    return None


def _priced_key(model: str) -> str | None:
    """The exact key for ``model``, else the most specific key it may inherit.

    Longest, not first in insertion order: "gpt-4o-mini-2024-07-18" starts with
    both "gpt-4o" and "gpt-4o-mini", so a first-match loop billed gpt-4o-mini as
    gpt-4o (16.7x), and an opus "-fast-<date>" as the cheaper non-fast base.
    """
    if model in _PRICING:
        return model
    return max((k for k in _PRICING if _inherits_row(model, k)), key=len, default=None)


def list_price(model: str) -> tuple[float, float] | None:
    """The published (input, output) USD price per million tokens a pricing row gives ``model``, or None.

    Unlike calculate_cost, which bills an unpriced model at the local rate or a
    default tier, this answers None when no row prices the id (a provider-qualified
    address resolves as in _pricing_key), so a caller that must bound spend before
    the call -- /connect's dictated seats -- refuses instead of guessing.
    """
    key = _pricing_key(model)
    if key is None:
        return None
    rates = _scheduled(key, _PRICING[key])
    return rates[0], rates[1]


def _request_rates(model: str, prompt_tokens: int) -> tuple[float, float, float, float]:
    """The rates a whole request bills at, given how many tokens its prompt holds.

    The base row answers for every model; a model in _REQUEST_TIERS moves to the
    highest tier its prompt exceeds. Haiku 5.5 is the case that made this real:
    a 100,001-token prompt bills five times the rate of a 100,000-token one.
    """
    rates = _resolve_pricing(model)
    for threshold, tier in _REQUEST_TIERS.get(_pricing_key(model), ()):
        if prompt_tokens > threshold:
            rates = tier
    return rates


def enforce_session_budget(tracker: CostTracker, projected_usd: float) -> None:
    """Block BEFORE spending if a query would push the session past the
    opt-in ceiling (R-3). Module-level by design: budget *enforcement* is a
    separate responsibility from cost *accounting* (SRP/ISP — CostTracker's
    public surface is unchanged). ZERO-REGRESSION: env unset -> returns
    immediately; no existing caller invokes this -> behaviour byte-identical.
    FAIL-OPEN: malformed ceiling -> no cap. Per-session cumulative via the
    tracker's already-public total_usd.

    Raises:
        BudgetExceeded: armed budget and spent + projected would exceed it.
    """
    ceiling = _read_session_budget()
    if ceiling is None:
        return
    spent = tracker.total_usd
    if spent + projected_usd > ceiling:
        raise BudgetExceeded(
            f"HAL session budget ${ceiling:.4f} would be exceeded: "
            f"spent ${spent:.4f} + projected ${projected_usd:.4f} "
            f"= ${spent + projected_usd:.4f}. Raise {_BUDGET_ENV} or "
            f"start a new session / reset() the tracker."
        )
