---
name: precog-idr
description: Precognitive orchestration-head IDR — emit a happi/1.1 signed envelope capturing the dispatch decision (effort tier, provider, fingerprint, predecessor chain) BEFORE delegating to HAL/downstream providers. Replay-keyed by prompt+kernel+routing fingerprint.
---

# PRECOG-IDR — Precognitive Orchestration Head

**Status**: Wave A SHIPPED (PR #3172, merged 2026-05-23). Wave B partial — replay cache + tests ship in this round; orchestrator integration deferred to a future round once LLM-call wiring is needed.
**Hypotheses**: H700–H705 registered with `pr_number=3172` (`learning/hypotheses.jsonl`). H700 *tier-half* partial-confirmed (50-prompt × 2-run, replay rate 1.0, zero LLM calls).
**Plan**: `plans/prompt-harness-opus-zesty-lightning.md`.

## What it is

A pure-deterministic decision layer that captures **WHY** a prompt was routed the way it was — emitted as a happi/1.1 IDR with sha256-placeholder signature (Ed25519 deferred to Wave C) and chained via `predecessor_idr`. The IDR is replayable: same fingerprint → same decision, regardless of session, machine, or wall-clock.

Three pure functions, zero LLM calls in the deterministic core:

| Function | Module | What it does |
|---|---|---|
| `compute_fingerprint(prompt, kernel_digest, routing_hint=None)` | `lib/precog/fingerprint.py` | sha256 over normalised(prompt) ∥ kernel ∥ hint — replay key. |
| `adaptive_effort_selector(prompt, kernel_keys, routing_hint, budget, hyp_backlog)` | `lib/precog/effort_selector.py` | Pure deterministic 5-tier map (low / medium / high / xhigh / max). No LLM. |
| `build_idr(prompt, fingerprint, decision, predecessor_idr, depth, ...)` | `lib/precog/idr.py` | Construct happi/1.1 envelope. `secrets.token_hex(4)` suffix on `id` prevents same-second collisions. |

Plus two cache primitives (Wave B partial):

| Function | Module | What it does |
|---|---|---|
| `cache_decision(fingerprint, decision, original_idr_id)` | `lib/precog/replay.py` | Atomic write to `state/precog/cache/<fp>.json`. |
| `replay_lookup(fingerprint, ttl_seconds=86400)` | `lib/precog/replay.py` | Return cached payload iff fresh, else None. Malformed → None (defensive). |
| `emit_replay_idr(...)` | `lib/precog/replay.py` | Build a `kind="precog-decision-replay"` IDR carrying `inputs.replay_of = original_idr_id`. Preserves audit chain. |

## Why "pure deterministic" matters here

`rules/exogenous-anchor-law.md` forbids self-evaluation. The PRECOG core is mutmut-stress-testable (H705) and replay-determinism-provable (H700) **without any LLM call** — because the core IS pure. The exogenous anchor for these two hypotheses is therefore the *test suite itself*: a test that fails when the implementation is wrong (anchor #1 — CI on mutation-surviving tests). The model that wrote the code cannot please an `assert a == b` on a deterministic function.

## H700 partial-confirmation evidence (zero-LLM probe)

`tests/test_h700_replay_determinism.py`:
- Population: 50 synthetic prompts across 5 shape buckets (trivial, claim-dense, code-heavy, multi-line, novel)
- Probe: `(fingerprint, effort_tier)` computed twice per prompt
- Result: 50/50 exact matches → replay_determinism_rate = 1.0
- Prediction was ≥ 0.95 → **confirmed_partial** (tier-half; provider_addresses half awaits Wave B orchestrator)
- Goodhart guard: separate test asserts the population exercises ≥2 distinct tiers (single-tier corpus = vacuous determinism)

## How a future caller uses this

```python
from lib.precog import (
    compute_fingerprint,
    adaptive_effort_selector,
    build_idr, append_idr,
    replay_lookup, emit_replay_idr,
)

# 1. Fingerprint the input
fp = compute_fingerprint(prompt, kernel_digest, routing_hint=hint_text)

# 2. Cache hit? Emit a replay IDR (preserves chain, zero re-reasoning cost).
cached = replay_lookup(fp)
if cached:
    idr = emit_replay_idr(
        prompt=prompt, fingerprint=fp, cached_payload=cached,
        predecessor_idr=parent_id, depth=parent_depth + 1,
    )
    append_idr(idr)
    return idr

# 3. Cache miss — pick effort tier, build decision, emit fresh IDR.
tier = adaptive_effort_selector(prompt, kernel_keys, routing_hint, budget, hyp_backlog)
decision = {
    "provider_addresses": [...],     # Wave B: orchestrator picks these
    "effort_tier": tier.value,
    # ... (other fields per plan)
}
idr = build_idr(prompt, fp, decision, predecessor_idr=parent_id,
                depth=parent_depth + 1)
append_idr(idr)
cache_decision(fp, decision, original_idr_id=idr.id)
```

## Slash command

`/precog [prompt]` — for manual probing only. Emits an IDR to `state/precog/idr.jsonl` and prints the chosen tier + fingerprint. **No LLM is invoked**; this is a deterministic readout of what tier/fingerprint the system would assign. Useful for:

- Verifying H700 (paste a prompt twice, confirm identical fingerprint+tier)
- Inspecting the tier-distribution claim (H703) on a representative prompt corpus
- Debugging fingerprint collisions or routing-hint sensitivity

Implementation: see the inline Python recipe above. The slash-command surface is intentionally thin — the value lives in the library, not the wrapper.

## What this skill does NOT do (deferred)

- **Pick providers** — `decision.provider_addresses` is populated by the orchestrator (Wave B), not by `effort_selector`.
- **Override `lib/hal/router.py::_route_quality_cost`** — wiring deferred until pilot proves the IDR contract holds end-to-end.
- **Use Ed25519** — current scheme is `sha256-placeholder`; Wave C lifts the audit field to real Ed25519 via `lib/copilot/audit_chain.py`.
- **Invoke any LLM** — the pure core is the point. The Wave B orchestrator will be the first PRECOG component that actually reasons; the pure-deterministic floor is what makes it auditable.

## Falsification

PRECOG is wrong if, over the next 90 days:

- H700 falls below 0.95 replay-determinism rate on a 50+ prompt sample (already 1.0 on tier-half) → fingerprint/effort-selector has hidden non-determinism
- H705 mutmut kill rate falls below 0.65 on `effort_selector.py` + `fingerprint.py` → weight constants and thresholds are not load-bearing (Goodhart failure)
- The replay cache produces a hit whose decision contradicts a fresh re-reasoning over the same fingerprint → cache invalidation contract violated (kernel digest must be in fingerprint; it already is)

Every failure case has a registered hypothesis with a deadline. None are advisory.

## Anchors

- Plan: `plans/prompt-harness-opus-zesty-lightning.md`
- Hypotheses: `learning/hypotheses.jsonl` entries H700–H705
- Origin PR: #3172 (Wave A merged 2026-05-23)
- Mesh deliberation issue (mutmut tooling path): #3174
- Sibling rules: `rules/exogenous-anchor-law.md`, `rules/scientific-method.md`, `rules/efficiency-rules.md` Rule 14 (Goodhart)
