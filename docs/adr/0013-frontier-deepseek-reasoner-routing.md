# ADR 0013 — `/frontier` → deepseek-reasoner; council openai speaker → grok-4.5

Date: 2026-07-17 · Status: accepted · Task: the DeepSeek reasoning-routing
cost-benefit decision (harness task #501) · Lane 2 of the forensic-report
ultraprompt.

## Context

- `/frontier` (the tier alias in `lib/active_llm.py::TIER_DEFAULTS`) pointed
  at `xai/grok-3` — **dead on both ends**: absent from
  `~/.hal/config/model_catalog.yaml` AND retired at the provider
  (`api.x.ai/v1/models` live-probe 2026-07-17 lists grok-4.20-0309-{reasoning,
  non-reasoning}, grok-4.20-multi-agent-0309, grok-4.3, grok-4.5, grok-build-0.1
  and imagine models only). Every `/frontier` invocation errored.
- The sovereign council's `openai` default speaker (gpt-4o-mini) is
  **quota-dead**: HTTP 429 on every call (observed 2026-07-16 across all runs;
  re-probed 2026-07-17 — 429 in 1.61s).
- Task #501 asked: is deepseek-reasoner worth routing to, versus
  llama-3.3-70b, in council/HAL?

## Measurements (fixed 3-probe set, temperature 0, direct provider calls, 2026-07-17)

| model | correct | latency (bug-spot / logic / review) | completion tokens (worst) |
|---|---|---|---|
| deepseek-reasoner | **3/3** | 4.71s / **133.5s** / 6.16s | 13,596 (13,490 reasoning) |
| deepseek-chat | 2/3 (waffled on the logic probe, committed to no answer) | 1.27s / 3.39s / 1.55s | 287 |
| llama-3.3-70b-versatile | 2/3* | 0.50s / 0.28s / 0.68s | 90 |
| openai gpt-4o-mini | — | HTTP 429 (dead) | — |
| xai grok-4.5 | not correctness-scored — availability probe only | 1.21s | — |

\* llama's bug-spot response matched the expected-substring scorer while
naming the WRONG line (it "fixed" the correct odd-length branch) — an honest
scoring correction applied after reading the transcripts; the raw scorer said
3/3. Deterministic substring scoring over-credits plausible-but-misplaced
fixes; transcripts are kept next to the numbers in the probe artefact.

## Decisions

1. **`TIER_DEFAULTS["frontier"] = "deepseek/deepseek-reasoner"`.** In-catalogue
   (`tier: frontier`), Pool-B (`rules/pool-a-offload-routing.md`), 3/3 on the
   probe set, and the model family the exogenous judge scored best in both
   recall design councils (seals `8acc9c14`, `bf070637`). A deliberate
   `/frontier` pin is a quality-over-latency choice — the 133s tail on hard
   prompts is the accepted price, and it is the CALLER's explicit choice.
2. **Council default roster: `openai` → `xai` (grok-4.5).** Preserves
   FOUR-provider diversity (deepseek / xai / gemini / groq) — the council's
   exogeneity rests on cross-provider disagreement, so diversity outranks
   doubling a deepseek leg. grok-4.5 live-verified.
3. **deepseek-reasoner registered as an addressable NON-default speaker**
   (`sovereign_council.REGISTRY["deepseek-reasoner"]`) for reasoning-heavy
   councils — not in the default fan-out (133.5s tail latency + 13k reasoning
   tokens would stall bounded-latency rounds).
4. **HAL catalogue gains live xai entries** (grok-4.5, grok-4.20-0309-reasoning)
   so `/llm` can address them; grok-3 stays absent (retired). Shipped as its
   own PR in the HAL repo — grip-platform/HAL#496 (the catalogue is a HAL
   artefact; this GRIP diff deliberately does not carry it).

## Falsification

Wrong if: (a) over the next ~10 `/frontier` pins the reasoner's tail latency
makes operators abandon the alias (then repoint at grok-4.5 or
gemini-3.1-pro and record why); (b) the xai council leg's error rate over the
next ~10 councils exceeds the dead-openai baseline it replaced (then swap to
the deepseek-reasoner leg and accept reduced provider diversity); (c) openai
quota recovers AND gpt-4o-mini measurably improves council quality (then
restore it — the swap was availability-driven, not quality-driven).
Probe artefact: session scratchpad `lane2/probe_results.json` (raw
transcripts + latencies + token usage).
