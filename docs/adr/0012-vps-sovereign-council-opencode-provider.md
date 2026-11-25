# ADR 0012 — VPS sovereign council as an OpenAI-compat opencode provider/model

**Status**: ACCEPTED — **architecture C′ decision-locked by V>> 2026-07-14** (the HITL pick in "Held for the operator" §1). Mechanism merged (#3995); activation now authorised and sequenced (see "Activation sequence" below).
**Date**: 2026-07-13 (proposed) · 2026-07-14 (locked)
**Owner**: V>>
**Deciders**: V>> (operator) + a zero-Anthropic sovereign deliberation (2 councils + SMT/Gentner gate)
**PR**: #3995 (mechanism) · concurrency-hardening + this lock (follow-up PR)
**Provenance**: sovereign-council seal `b959faae9bcd702a6b5634f4be82e574d8580441c125934bc3d8bda8dc36ca19`, IDR leaf `precog-1783976964-75896244` (zero Anthropic). Re-verify: `grip-sovereign-council --verify b959faae9bcd702a`.

> **Decision lock (V>> 2026-07-14):** C′ confirmed as the chosen architecture and the
> intended opencode default. Activation is authorised. The wiring MUST land as one
> coordinated cross-repo change so the picker never points at a route that does not
> yet exist (feature-complete: no partial-domain stub): `config/providers.yaml` +
> `config/opencode-sovereign.json` (GRIP) land **together with** the `~/.hal` `:4010`
> `council` fail-open route (#433) and the VPS deploy of `bin/grip-council-endpoint`
> (#434); the `grip-harness-model --accept opencode council` default-flip is the last
> step, after the fail-open path is tested (its mitigation is theatre until proven).

---

## Context

V>> asked to make "the VPS itself a `provider/model` in the opencode launch picker — the canonical HAL of the HALs — and the default": one selectable model that is actually a sealed, non-Anthropic (Pool-B) sovereign **council** running behind the shim.

Grounded mechanism (verified origin/main):
- The launch picker (`lib/grip_harness_model.py`) reads `config/providers.yaml`; each `type: cloud` provider with a static `models:` list becomes a `provider/model_id` row, forwarded to the local HAL gateway `:4010` which routes by model family.
- OpenCode's real routing lives in `config/opencode-sovereign.json` → `~/.config/opencode/opencode.json`.
- `bin/grip-sovereign-council` was a sealed **CLI**, not a server — it needed an OpenAI-compat wrapper to become a "model".
- The opencode default is per-harness + reversible: `grip-harness-model --accept opencode <id>`.

## Decision

1. **Architecture C′** — the VPS convenes the council behind an OpenAI-compat route on the **existing** `hal-server:3850` (reuse; no new public surface / tunnel); the local `:4010` gateway routes the `council` family **downstream** to the VPS and **owns retry + fail-open** to a single fast Pool-B model, so the launch default never dies when the VPS is unreachable.
2. **Adopt the architecture; reject the slogan.** The model id is literally **`council`** / `sovereign-council`, described plainly. It is **NOT** marketed as "a HAL" — see Alternatives → the Gentner gate.
3. **Ship the mechanism now (reversible, tested); hold activation for the operator.** The mechanism (`lib/sovereign_council.py` extraction + `bin/grip-council-endpoint` + tests) merges under CI/AGORA; the config wiring, the `~/.hal` `:4010` route, the default-flip, and the live VPS deploy are operator-gated.

## Alternatives considered

| Alt | Summary | Why not (lead) |
|-----|---------|----------------|
| **A** | New `your-gateway.example.com` OpenAI-compat server + tunnel | Violates REUSE: new public surface, Cloudflare route, pm2 service, TLS/auth boundary — duplicates `hal-server:3850` for zero capability gain. |
| **B** | Local `:4010` runs `council` locally, no VPS | Directly contradicts the ask ("the VPS itself is the provider"); loses on-VPS secret resolution + the VPS-generated seal. |
| **C** | VPS route, opencode points **directly** at `your-gateway.example.com/v1` (no local hop) | Won the sealed sovereign-council on literalness+reuse; **retained as a live HITL option** (most Windows-portable — DNS only, no local daemon). Lead is C′ only for its **fail-open**: on a default selected every launch, availability > ~200ms of "VPS-is-provider" literalness. |

**The Gentner gate is load-bearing.** The mechanical structure-mapping enforcer **DENIED** "the canonical HAL of the HALs" at **29/100** as a *surface* analogy: a HAL is **reactive failover** (sentinel + provider-ladder); a council is **deliberative exogenous judgment** (quorum + a judge that is a *different* provider, per `exogenous-anchor-law.md §3`). Different causal structures serving different goals — asserting a systematicity the code (`lib/hal/sentinel.py` vs the council) does not have. The `external_smt_scorer` "allow" at 86/100 ran **degraded** (its external council errored) and scored a *narrower* mapping. Resolution: the narrow, sound relational mapping (one-request → N-fan → single judged reduction; multiplicity hidden behind one interface) is why a `council` **model** slots into the picker at all — but the *slogan* is dropped.

## Consequences / risks

- **SPOF + latency** (both councils' #1): a council turn is ~11–18s and dies if the VPS is down. C′'s local fail-open mitigates — the failover path MUST be tested when it lands, or the mitigation is theatre.
- **Seal-transport**: the sha256+IDR seal is built on the VPS and must survive the hops (headers + `x_grip_council` body block). Covered by the seal-through-endpoint test.
- **Hot-path regression**: the `:4010` `council` route (in `~/.hal`) must isolate the new family; every other id uses the unchanged path (passthrough test).
- **Cost-on-default**: a 4-provider fan-out as the default spends 4+ Pool-B calls per launch (~$0, but rate-limited — an OpenAI speaker 429'd in the sealed run); fail-open degrades under quota.
- **Exogeneity of this decision's own validation**: the SMT-scorer ran degraded — its 86 is a mechanical-floor pass, NOT exogenous confirmation. The real anchor for shipping is **CI on mutation-surviving tests + AGORA**, not the score.

## Concurrency model (added by the #3995 DEFER follow-up, task #430)

`bin/grip-council-endpoint` runs a `ThreadingHTTPServer` — one thread per request,
each convening its own council. Shared cross-request state, and how each piece is
made safe:

- **Speaker roster** — `run_council` copies (`list(speakers or DEFAULT_SPEAKERS)`);
  no run can mutate the module-global roster or a caller's list.
- **Provider keys** — memoised per process under `_KEY_CACHE_LOCK`: one
  resolution per service (env → Keychain → `/proc`), shared by all speaker
  threads. A bare lock around the `/proc` read alone was **evaluated and
  rejected** — serialising our readers cannot stop the *target process*
  rotating keys between reads; the memoisation is what buys per-process
  consistency. Consequence: rotating a provider key under a long-lived
  endpoint requires a process restart (`pm2 restart grip-council-endpoint`).
- **IDR chain append** — already atomic at source: `lib/precog/idr.py::append_idr`
  holds `fcntl.flock(LOCK_EX)` + fsync per line (same pattern as the #419
  ultralearn ledger fix). Verified, not re-fixed here.
- **Seal files** — keyed by content sha (`state/sovereign-council/<sha16>.json`):
  distinct prompts → distinct paths; identical content → identical bytes, so
  concurrent writers cannot corrupt each other's records.

Anchored by `tests/test_council_concurrency.py` (simultaneous POSTs → unique,
independently re-verifying seals — the falsification the AGORA judge named).

## Held for the operator (`/back`)

1. Pick the architecture: **C′ (recommended)** vs C vs A.
2. Wire the picker: `config/providers.yaml` `grip-vps/council` + `config/opencode-sovereign.json`.
3. Add the `~/.hal` `:4010` `council` fail-open route (C′ only).
4. `grip-harness-model --accept opencode council` (reversible; reset → `gpt-5.5`).
5. Deploy `bin/grip-council-endpoint` on the VPS behind `hal-server:3850`.

## Hypothesis (falsifiable)

Registered against PR #3995, deadline **2026-08-15**. Wrong if: the seal does not re-verify end-to-end (seal-transport defect — fix, don't drop the seal); OR the fail-open misfires and a VPS outage kills the default; OR the `council` route regresses an existing HAL family; OR council latency makes the default unusable in practice (then fast-fail-open threshold, not abandon-council).

## Review

- **Review date**: 2026-08-15 (with the hypothesis).
- **Success metric**: 12 tests green in CI + AGORA PASS + (post-deploy) a council reply's `X-Grip-Council-Sha` re-verifies `VERIFIED` + `seal.anthropic_calls == 0` over ≥20 real turns + 0 regressions on existing families + ≥95% of turns survive a forced-down VPS via fail-open.
- **Reopen trigger**: fail-open proves untestable/theatre; or operators reject council latency outright.
