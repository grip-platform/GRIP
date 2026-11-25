# ADR 0011 — Broly Council CCH-Billing Mandate

| Field | Value |
|-------|-------|
| Status | Accepted |
| Date | 2026-06-23 |
| Deciders | V>> (operator), Broly Council (Speaker B, Speaker C) |
| Issue | [GRIP #3659](https://github.com/grip-platform/GRIP/issues/3659) |
| Source | AGORA DEFER on PR #3659; extracted from `lib/broly_council.py:dispatch_role` docstring |

## Context

`lib/broly_council.py` dispatches per-role LLM calls for the Broly council
(analyst, explorer, reviewer, pragmatist, devil, integrator). Each role maps
to a HAL provider/model. The council is **class-B GRIP-internal machinery** —
it runs autonomously, potentially overnight, and can produce many LLM calls
per evaluation cycle.

Anthropic offers two billing paths:
1. **Per-token API key** (`grip-anthropic-external` keychain entry → `ANTHROPIC_API_KEY`)
   — unbounded, pay-as-you-go consumption.
2. **CCH (Claude Code subscription)** (`lib/hal/utils/cch.py` → OAuth token +
   `x-anthropic-billing-header`) — bills the shared **weekly-capped** bucket,
   also used by the operator's interactive `claude` REPL.

## Decision

**CCH billing is FORBIDDEN for broly council dispatch.** Until HAL carries an
enforceable pre-dispatch billing-class directive, Broly must reject
`anthropic/*` role assignments and use non-Anthropic providers.

### Rationale

Routing broly (class-B internal machinery) through CCH would let an overnight
broly-heavy loop silently exhaust the operator's interactive weekly cap,
leaving the operator unable to use `claude` in their own session until the cap
resets.

### Fallback ladder

Broly uses a non-Anthropic default roster. Provider failures fall back to the
**econ-default NON-Anthropic ladder** (groq/gemini), **never** to CCH. This is a
monotone-toward-safe design: the fallback may only move AWAY from the weekly
cap, never onto it.

### Cross-references

- `docs/adr/0009-idr-forest-spec.md` — HAPPI-via-CCH usage (P4, future sprint)
- `lib/hal_llm_adapter.py` — `grip_infer` cascade (CCH -> Kimi -> cheap -> local)
- `rules/exogenous-anchor-law.md` — the anthropic+xai+groq diversity that makes
  a council verdict a valid exogenous anchor

## Consequences

1. A runtime guard rejects every `anthropic/*` Broly assignment before the
   first provider call (`lib/broly_council.py:validate_cch_billing_guard`).
2. A local API key is not accepted as proof of the HAL server's billing route:
   the server must eventually expose an enforceable pre-dispatch directive
   before Anthropic seats can return.
3. `DEFAULT_ROLE_ASSIGNMENTS` must not reference the old `claude-opus-4-7`
   model ID (a regression-anchor test enforces this).
4. A regression test proves a rejected Anthropic assignment makes zero
   `_query_grip_infer` calls.

## 2026-07-30 falsifier

An ultracode criterion mesh ran after `/llm cheap`, yet its six explicit role
assignments produced four signed `anthropic-cch` ledger entries (sequences
71, 73, 75, and 76). The old guard ran only after a failed primary result, so a
successful CCH response bypassed it. Moving the guard before dispatch and using
a non-Anthropic default roster closes the measured path.

The post-fix live mesh produced sequences 77–82 through Gemini, XAI, DeepSeek,
Gemini, XAI, and Gemini respectively: zero CCH calls, with the signed usage
chain verified at 82/82.
