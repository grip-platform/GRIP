# IDR Forest — Specification (P0 + P1)

| Field | Value |
|-------|-------|
| Status | Accepted (P0 + P1) |
| Date | 2026-05-24 |
| Deciders | V>> (operator) |
| Sprint | `plans/tender-popping-catmull.md` Wave 3-A |
| Issue | [#2799](https://github.com/grip-platform/GRIP/issues/2799) — Prove the IDR loop works without circularity |
| Plan master | `drafts/ultraplan-idr-replay-forest-banach-2026-05-17.md` (V>>-ratified) |
| Hypotheses | H-FOREST-1, H-IDEMPOTENCE-1 (deadline 2026-07-05) |
| Sibling | ADR 0008 (ambitious-workflows federation) |

## Context — what "prove it works" means (the I/Q split)

Per the Integrity-vs-Quality Theorem (ultraplan §0), the IDR loop's proof is **two disjoint claims** proven by **different means**. Conflating them is the failure mode this spec exists to prevent.

| Claim class | Statement | Proof means | Self-provable? |
|---|---|---|---|
| **I — Integrity** | The IDR ledger is tamper-evident; replay is deterministic; the forest projection ℱ is idempotent (stable). | Cryptography (Ed25519 via `lib/audit_chain.py`) + deterministic re-execution + fixed-point math (`lib/banach_projector.py`). | YES |
| **Q — Quality** | The decisions were good; a re-derived IDR is *better*. | Exogenous oracle ONLY (CI / benchmark / human / cross-provider, per `rules/exogenous-anchor-law.md`). | NO |

This spec covers **I only**. Q lives in P3+ and is deliberately out of scope for this PR.

## Forest data structure (locked schema)

```
Forest
├── roots:  tuple[str, ...]                    # exogenous anchor ids ONLY
├── nodes:  dict[str, ForestNode]              # id → node
└── edges:  dict[str, str | None]              # child_id → parent_id (None at root edge)

ForestNode
├── idr:        PrecogIDR                      # the happi/1.1 envelope (lib/precog/idr.py)
└── anchor_id:  str | None                     # nearest exogenous-root ancestor; None ⇒ unanchored
```

### Root admissibility (C3 — exogenous-anchor law)

A `root` is a string id of one of the four admissible exogenous anchors enumerated in `rules/exogenous-anchor-law.md`:

1. CI run id on a mutation-surviving suite (`ci:<run-id>`)
2. Human-authored commit/merge SHA (`human:<sha>`)
3. Cross-provider verdict id (`council:<id>`)
4. Pre-registered hypothesis id (`hypo:<H-id>`)

A node with no exogenous-root ancestor is **unanchored** by definition (`anchor_id is None`) and flagged. It is NEVER trusted — never replayed, never replicated, never quoted as evidence.

### Node admissibility

A `node` is a `PrecogIDR` envelope from `lib/precog/idr.py` (happi/1.1; `kind`, `id`, `predecessor_idr`, `depth`, `fingerprint`, `ts`, `decision`, `inputs`, `audit`). The `audit.signature` field carries the chain-hash; verification routes through `lib/audit_chain.verify_entry` (Ed25519 Round 6).

### Edge admissibility

Edge `b → a` exists iff IDR `b`'s `predecessor_idr == a.id`. Causality is **expressed by the predecessor pointer**, not by ambient context. A node with `predecessor_idr is None` is a candidate root iff its id matches one of the admissible-anchor patterns above; otherwise it is unanchored.

## ℱ (the Banach projector) — what it proves, what it does NOT

`canonicalise(forest) → Forest` is the **canonicalising re-projection**:

1. For each node, re-canonicalise its IDR JSON (`_canonical_json(asdict(idr))`) — drops formatting differences, sorts keys, strips whitespace.
2. Re-derive every chain-hash from `inputs_hash` via `audit_chain.compute_chain_hash` so any byte-level drift surfaces.
3. Re-link edges from `predecessor_idr` pointers.

The substrate is correct-by-construction iff ℱ is a **contraction** on the metric space of forests (edit-distance metric — Wave 3-A P3 wires the metric, this PR locks the contract). Repeated application converges to a **unique fixed point**, and `ℱ(canonical) = canonical` (idempotence).

**What ℱ proves**: the forest is internally consistent and stable — re-projection introduces no drift; there is one canonical form (Integrity).

**What ℱ does NOT prove**: that the fixed point is *correct*. A contraction converges to a unique fixed point regardless of whether that point encodes good decisions. `ℱ(x) = x` is a *consistency* certificate, not a *quality* certificate. (Doc-1 theorem, restated where it bites.)

## Replay protocol (P0 spec; P1 deterministic only; P2+ cross-provider)

1. **Deterministic IDRs** (no AI): an independent runner re-derives the decision from `inputs_hash`. Re-derived envelope must byte-match. Divergence ⇒ integrity failure (recorded ≠ reproducible) ⇒ chain rejected.
2. **AI-bearing IDRs**: cannot byte-match (non-determinism). The cross-provider re-decision is recorded as a *new* IDR linked to the old; *agreement rate* is telemetry (lowers confidence if low, **never raises**). Deferred to P2 (`lib/hal_llm_adapter.py` cross-provider).
3. **Signature verify** reuses the EXACT verify code, never a heuristic (per `feedback_chain_heal_signed_count_highwater_floor.md`): `valid-signed-count` must equal `state high-water` or replay refuses.

## Test classes (T-I1…T-I4)

| Test | Statement | Status after P1 | Wave |
|---|---|---|---|
| **T-I1** Tamper detection | Flipping any byte of a node's IDR invalidates the chain — `verify_chain_integrity` returns `INVALID` | **passing** | P1 ships |
| **T-I2** Deterministic replay byte-match | Same input ⇒ same IDR; re-derived envelope byte-equal to recorded | **xfail** (P2 wires cross-provider runner) | P2 |
| **T-I3** Idempotence | `canonicalise(canonicalise(F)) == canonicalise(F)` over the test corpus | **passing** | P1 ships |
| **T-I4** Contraction | distance(`canonicalise(F)`, fixed-point) is monotone-non-increasing under repeated ℱ application | **xfail** (P3 wires metric space) | P3 |

Every test is **mutation-anchored** (Rule 14): a degenerate implementation (always-return-VALID / always-equal / no-op canonicalise) fails the test.

## Hypotheses (registered against this PR)

| H-id | Claim | Falsified if | Deadline |
|---|---|---|---|
| H-FOREST-1 | Forest contraction toward anchors is monotone-non-increasing under ℱ over a held-out corpus of ≥10 synthetic forests | distance increases on ≥1 application across the corpus | 2026-07-05 |
| H-IDEMPOTENCE-1 | ℱ is idempotent over the test corpus (`canonicalise(canonicalise(F)) == canonicalise(F)` byte-equal in serialised form) over ≥10 synthetic forests | any non-equal pair surfaces over the corpus | 2026-07-05 |

## Phasing (each phase exogenously gated; no self-score)

| Phase | Deliverable | Gate |
|---|---|---|
| **P0** | This spec + xfail T-I1..T-I4 stubs | (this PR) V>>-lifted |
| **P1** | `lib/idr_forest.py` Forest dataclass + canonicalise + verify_chain_integrity; T-I1 + T-I3 flipped to passing | (this PR) CI + human merge |
| P2 | Independent cross-provider replay executor (`lib/hal_llm_adapter.py`) → T-I2 passing | future sprint; CI + cross-provider verdict |
| P3 | Edit-distance metric + contraction proof harness → T-I4 passing | future sprint; CI + human + H-FOREST-1 verify |
| P4 | `/grip-anywhere` external read + syscall-coverage proof of HAPPI-via-CCH | future sprint; external analyzer + `bin/syscall-coverage` meter |

## Out of scope (explicit)

- T-Q1 quality oracle (P3 territory, gated on exogenous benchmark design)
- Cross-provider replay (P2)
- Edit-distance metric implementation (P3)
- `/grip-anywhere` integration (P4)
- Migration of legacy IDR chains in `state/precog/idr.jsonl` (this PR is additive; chains stay untouched)
- Performance optimisation (Forest construction is O(N) per node; canonicalise is O(N log N) over node count via dict ops; P1 ships correctness, not throughput)

## Falsification (the spec itself)

This spec is wrong if:

1. Integrity tests pass while a known-bad decision survives undetected — the I/Q split leaked (Q rode on I).
2. ℱ is not a contraction over the test corpus (no unique fixed point ⇒ "stable" is meaningless) — replace the projector, not the claim.
3. T-I1's mutation injection passes a degenerate (always-INVALID) implementation — the test is too weak; tighten its assertions.
4. The unanchored-detection rule rejects legitimate exogenous-root nodes — the anchor-id pattern set is incomplete; extend `EXOGENOUS_ANCHOR_PATTERNS`, do not weaken the check.

## References

- `drafts/ultraplan-idr-replay-forest-banach-2026-05-17.md` — V>>-ratified master plan (§§0–9)
- `plans/tender-popping-catmull.md` — sprint plan (W3-A atom)
- `lib/precog/idr.py` — PrecogIDR envelope + canonical JSON + append/read
- `lib/audit_chain.py` — Ed25519 signing + verify_entry tri-state + chain hashing
- `lib/banach_projector.py` — fixed-point kernel + joint_norm + banach_project
- `lib/hal_llm_adapter.py` — cross-provider AI syscall shim (P2)
- `rules/exogenous-anchor-law.md` — C3 admissible-anchor enumeration
- `rules/scientific-method.md` — hypothesis engine + shadow metrics
- `rules/verify-canonical-not-claimed.md` — replay verification anchors
- `rules/knowledge-maturation-functor.md` — this is the N=1 instance of "Forest schema for IDR provenance"

Primary Author: V>>
