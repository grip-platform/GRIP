# Two-Pool LLM Routing — Offload Cognition to HAL (Pool B), Spare the Weekly Limit (PARAMOUNT)

Origin: V>> 2026-07-06, weekly-limit crunch (session `8ede4310`). `/usage` hit
**79% weekly (all models) / 85% Fable**, resetting Jul 12 03:00 SAST. The forcing
function to actually *use* the sovereign HAL substrate GRIP already proved live
(2026-06-27 sovereignty capstone). Grounding plan (do not re-derive):
`plans/hal-offload-weekly-limit-sovereignty.md`. Rule-at-N=1 per
`knowledge-maturation-functor.md` (severe: an active operator-blocking limit).

## The one insight — separate spend pools, and only Pool A is the `/usage` weekly limit

| Pool | What runs here | Weekly-limited? | Measured by |
|---|---|---|---|
| **A — Anthropic harness** | the interactive Claude Code REPL (Opus), `claude -p` relay/batch spawns, **Claude Code `Task`/`Agent` subagents** (incl. Workflow `agent()`, the Agent-tool `broly` at opus/max, `/ultrawin` fleet, `/ultrado` waves) | **YES — this IS the `/usage` %** | `/usage` (the CC panel sees ONLY Pool A) |
| **B — HAL `grip_infer`** | GRIP's programmatic LLM calls → `lib/hal_llm_adapter.py::_query_grip_infer` → RAILLM → groq / gemini / deepseek / qwen / xai / local — including the **metered Kimi API** at `api.moonshot.ai` (keychain `grip-moonshot`) | **NO — separate spend, ~$0** | `grip-llm-usage` (tamper-evident signed chain) |
| **C — OpenAI ChatGPT subscription** | Codex on `bin/grip-codex-ladder` rung 1 (`-c model_provider=openai -c forced_login_method=chatgpt`); `bin/grip-codex` delegates there by default. Codex pointed at the HAL gateway is NOT this pool — it bills B (`bin/grip-codex:59-76`, corrected 2026-08-31) | **NO for `/usage`** — the operator's separate OpenAI plan limits | OpenAI's own plan usage; `bin/grip-codex-ladder --status` for account readiness |
| **D — Kimi Code subscription** | Moonshot's Kimi Code plan at `https://api.kimi.com/coding/` — ids `k3`, `k3-256k`, `kimi-for-coding`, `kimi-for-coding-highspeed`; a weekly quota plus a rolling 5-hour window shared across the Kimi CLI, VS Code and third-party tools. **ARMED ONLY when the keychain entry `grip-kimi-coding` exists** (`lib/next_move.py::pool_d_armed`); today it does not, so nothing routes here | **NO for `/usage`** — its own weekly quota + 5-hour window | nothing yet — the quota probe lands with the HAL `kimi-coding` provider (a separate PR) |

## Pools are spend accounts, never harnesses or model families

A pool names **whose allowance a call draws down**, not which binary ran it or which
model family answered. Codex is a harness: on `bin/grip-codex-ladder` rung 1 it spends
Pool C, on the HAL gateway it spends Pool B (`bin/grip-codex:59-76`). Kimi is a model
family: the metered `api.moonshot.ai` key is a Pool B backend, while the Kimi Code plan
is its own time-windowed allowance and therefore its own pool, D. Putting a Kimi id on
the ChatGPT rung would be a category error, which is why D is not folded into C.

Ruled 2026-09-07 by a sealed non-Anthropic sovereign council (speakers deepseek, openai,
gemini, groq; judge `gemini-3.1-pro-preview`), which chose "a new Pool D for the Kimi
Code subscription, armed only when a `grip-kimi-coding` credential exists; the metered
Kimi API stays a Pool B backend; neither goes in Pool C" over widening C and over
Pool-B-only. Seal sha256
`e49c00c214c500be81c72cfe97e7f484988dea8e128db43c5d301aa7ab302bcb`, IDR leaf
`precog-1788805766-c58539b1`; re-verify with
`bin/grip-sovereign-council --verify e49c00c214c500be`. The judge's strongest objection
is kept as the arming rule: a new pool adds routing, accounting and tests for a credential
GRIP does not hold today, so nothing built for D may act until that keychain entry exists.

Falsifier: if the Kimi Code quota turns out to be metered per token rather than a
time-windowed allowance, D collapses into B and its row is removed.

## The Law

> **LLM-heavy cognition routes through Pool B (HAL) by DEFAULT. Pool A is spent
> only on what structurally cannot move — the interactive REPL's own reasoning
> and any deliberately-Anthropic voice in a cross-provider council. A council,
> review, deliberation, or autonomous/batch job that runs as a Claude Code
> subagent or `claude -p` when a HAL Pool-B primitive would do the same work is a
> weekly-limit leak.**

## The reclaim is at the DECISION layer, not a missing primitive (the load-bearing why)

Verify-canonical, 2026-07-06: GRIP's *programmatic* work is **already ~94%
sovereign** — `grip-llm-usage` (chain VERIFIED, 35 calls): groq 57% · gemini 31% ·
xai 6% · **anthropic-cch 6% (2 stale calls, last 2026-06-26)**. Pool B is not the
problem. The reclaimable weekly spend is Pool A — specifically the **63% from
subagent-heavy sessions** + **11% from `/ultrawin` subagents** + **44% while 4+
parallel sessions ran**. Those are *default choices*, not absent capability: the
HAL Pool-B council primitives already exist and are proven —

- **`grip-council --roster open`** (`lib/hal_council_ideate.py`) — multi-provider, non-Anthropic roster, HAPPI seam. $0 weekly.
- **`grip-sovereign-council`** — N non-Anthropic speakers + a *different* non-Anthropic judge (exogenous by construction) + sha256 prove-it seal.
- **`lib/broly_council.py`** — routes every voice through `_query_grip_infer`
  using a mechanically non-Anthropic default roster. Preferred entrypoint:
  `lib/broly_ultracode.py::evaluate_criterion_mesh_idempotent` — the same
  Pool-B path under the `lib/ultracode` kernel, so resumed/replayed councils
  cost 0 new calls. Explicit `anthropic/*` Broly assignments fail before
  dispatch: `/llm pin` cannot override an explicit model, and a local API key
  does not prove which billing path the HAL server will choose.
- **`lib/ultracode` adapters** — `HALAdapter` (kernel) and `GripInferAdapter` (broly consumer) both bottom out in `_query_grip_infer` (Pool B); the kernel changes how OFTEN tokens are spent (content-hash idempotency), never WHERE.
- **`grip-opencode run -m deepseek/deepseek-chat "…"`** — non-interactive Pool-B inference; the batch/autonomous replacement for `claude -p`.

So the fix is behavioural: **stop reaching for the Agent-tool `broly` (opus/max) or
`claude -p` when a HAL council does the same work for free.** This rule is
push-based (loaded every session) precisely because the 63% lives in that reflex.

## Routing default (apply before spawning any council / review / deliberation / batch)

1. **Need a council / multi-voice deliberation / ideation?** → `grip-council --roster open` or `grip-sovereign-council` or `lib/broly_council.py` (Pool B). NOT the Agent-tool `broly` and NOT a Workflow `agent()` fan-out for the *cognition*. Keep the exogenous **non-Anthropic** judge — the `open`/sovereign rosters already satisfy `exogenous-anchor-law.md` (a different non-Anthropic model grades; no model grades its own homework).
2. **Long / heavy / >150k-context task?** → `RAILLMSentinel.dispatch_with_history` (InfiniteMachine, `~/.hal/lib/hal/raillm.py`) on cheap frontier models, not one 200k Opus REPL window.
3. **Autonomous / batch / overnight?** → `grip-opencode run` routed at the HAL gateway (`:4010`), NOT `claude -p` (Pool A).
4. **Before any `/ultrawin` / `/ultrado` fan-out** → `/llm pin cheap` so deliberators default to groq/free, and prefer HAL councils over Agent-tool subagents for the deliberation legs.
5. **Close the fallback leak** → keep the `/llm` marker pinned to a Pool-B provider (`/llm pin` → `groq/llama-3.3-70b-versatile`); an UNSET marker lets `grip_infer` fall back to `anthropic-cch` (the 2 leaked calls prove it). `grip-llm-usage verify` is the gate.

## Honest boundaries (so this is not theatre)

- HAL **cannot** offload the interactive REPL — its reasoning is Opus, Pool A,
  weekly-limited. Only Pool B moves. This rule never claims otherwise.
- A **deliberately-Anthropic council speaker** (e.g. AGORA's `claude -p` Speaker
  paired with the Gemini judge) is a design choice, not a leak — do not rip it out
  unilaterally (regression risk on the CI reviewer). Swap it to the non-Anthropic
  `open` roster only under an explicit, gated, reversible weekly-limit-pressure
  mode, through AGORA's own review.
- **Sovereignty is verified, never assumed** — `grip-llm-usage verify` (BROKEN on
  tamper) is the proof the fan-out actually spared the limit.

## Falsification

Wrong if, over the ≥10 sessions it governs: (a) after routing councils/reviews to
Pool B the `/usage` weekly slope does **not** drop (subagents weren't the driver →
the rule targets the wrong pool); OR (b) HAL-council verdicts regress vs
CC-subagent councils on a re-review spot-check against the exogenous anchor
(quality lost → keep the Anthropic voice); OR (c) the `/llm pin` causes
`anthropic-cch` fallbacks to **spike** in `grip-llm-usage` (key/config gap →
sovereignty illusory, fix the key not the rule). Track: `/usage` weekly % slope
before/after · `grip-llm-usage` anthropic-cch share (target ≤6%, no spike) ·
council-quality spot-check. Hypothesis: **H732** (deadline 2026-07-20).
Goodhart-safe: success = weekly-slope-reduction at equal review quality, never
count of Pool-B calls.

## Scope

Every council / review / deliberation / ideation / autonomous / batch decision in
GRIP and HAL. Sits beside `exogenous-anchor-law.md` (the judge stays exogenous +
non-Anthropic), `dispatch-economics.md` (WHEN to dispatch at all — this adds WHICH
pool the cognition lands in), `rate-limit-never-blocks.md` (a 429 fails over, never
halts), `efficient-always-active.md` (depth-0 main-thread bias), and
`syscall-doctrine.md` (`grip_infer` is the Pool-B shim every LLM call routes
through). Mirror into `~/.hal/rules/`. The interactive REPL stays frugal — short
context, `/clear` between tasks, never 4+ parallel Anthropic sessions.
