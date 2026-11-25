# ADR 0008 — Ambitious Workflows Federation

| Field | Value |
|-------|-------|
| Status | Accepted |
| Date | 2026-05-24 |
| Deciders | V>> (operator) |
| Carve-out from | Phase 1 grounding (`drafts/ambitious-workflows-deliberation/00-grounding.md`) |
| Hypotheses | H-SWARM-MANIFEST-1, H-SWARM-DISPATCH-ECON-1 (deadline 2026-07-05) |
| Sibling ADRs | [0001 — AI Syscall Canonical Shim](./0001-ai-syscall-canonical-shim.md), [0007 — Embedding Model Pluggability](./0007-embedding-model-pluggability.md) |
| Plan | `plans/tender-popping-catmull.md` Wave 2 |
| Issue | [#3023](https://github.com/grip-platform/GRIP/issues/3023) — RSI ambitious-workflows |

## Context

GRIP/HAL already ship the building blocks for ambitious autonomous workflows. The Phase 1 grounding doc inventoried what exists vs the real gaps; this ADR ratifies that inventory and locks the constraints every implementation must reconcile.

| Substrate | Status | Used by |
|---|---|---|
| `lib/grip-worktree.sh` (3-step git-crypt-safe worktree) | shipped | C1 |
| `skills/bg-dispatch/SKILL.md` (Shape A/B/C dispatch tree) | shipped | C2, C4 |
| `rules/ci-review-driven-convergence.md` (deploy→CI→fix loop) | shipped | C3, C6 |
| `rules/verify-canonical-not-claimed.md` (blob + endpoint + e2e) | shipped | C6 |
| `rules/exogenous-anchor-law.md` (external-anchor requirement) | shipped | C3 |
| `lib/hypothesis_engine.py` (pre-registration) | shipped | C3 |
| `rules/HOLISTIC.md` kernel + R1 ledger | shipped | (memory) |
| `lib/kono_preload.py` warm-start injection | shipped | (memory) |
| `lib/bg_dispatch.py` reference pre-fetch shim | shipped | dispatch envelope |
| **Swarm coordinator** (fan out N atoms → N worktrees → monitor → merge) | **absent** | Workstream 1 |
| **Post-merge live-endpoint probe loop** (≥2 independent signals) | **absent** | Workstream 2 |
| **Steering inbox + live-state manifest** (session-start reconciliation) | **absent** | Workstream 3 |

This ADR ships the **schema and validators** for Workstream 1 in `lib/worktree_swarm.py`, plus the locked contracts for Workstreams 2 and 3 so future sprints have a stable surface to build against.

## Decision

### Workstream 1 — Worktree-Isolated Parallel Agent Swarms

**This PR ships**: `lib/worktree_swarm.py` with
- `WorkstreamAtom` / `WorkstreamManifest` frozen dataclasses (manifest schema)
- `validate_disjoint_ownership(manifest)` (C4 — fail-fast on shared writes)
- `select_shape(manifest)` (C2 — mechanical dispatch-economics gate; A/B/C)
- `dispatch_fanout(manifest)` STUB raising `NotImplementedError("W5-D wires this")` so callers fail loudly rather than silently sequential-falling-back

**W5-D wires** (gated on this PR landing): `dispatch_fanout()` actually creates N worktrees via `grip-worktree.sh add` and dispatches one `Agent(run_in_background=True)` per worktree. One additional test extending `test_worktree_swarm_manifest.py`.

### Workstream 2 — Self-Verifying Production Deploy Loop

**This PR ships**: the **verification-contract schema** below as a locked contract. Implementation (`lib/deploy_verify_loop.py`) is a separate sprint.

```python
@dataclass(frozen=True)
class VerificationProbe:
    endpoint: str                              # URL or filesystem path
    assertion: Callable[[object], bool]        # predicate over probe response
    label: str                                 # human-readable for reports
    signal_class: str                          # "CI"|"endpoint"|"council"|"hypothesis"

@dataclass(frozen=True)
class VerificationContract:
    probes: tuple[VerificationProbe, ...]
    min_signal_classes: int                    # ≥2 distinct signal_class values to converge
    pre_registered_hypothesis: str             # H-* id; REQUIRED (C3)
```

`run_deploy_verify_loop(contract)` (future) returns CONVERGED only when the number of *distinct* signal classes with passing probes ≥ `min_signal_classes` AND the registered hypothesis verification status is CONFIRMED. Self-grade impossible (C6).

### Workstream 3 — Cross-Session Memory & Steering Substrate Hardening

**This PR ships**: the spec rows below as a locked contract. Implementation lives in future sprints; all substrate writers MUST route through a single Shape B coordinator (C4).

| Surface | Contract | Implementation gate |
|---|---|---|
| Steering inbox at session start | bounded < 200 tokens; read-only injection alongside HOLISTIC.md | future sprint |
| Live-state manifest | `state/session/live-state.json` carries `{branch, last_pr_sha, deploy_state, active_worktrees}`; reconciled against git/deploy reality at session start | future sprint |
| Duplicate-creation guard | memory/skill/rule writes check existing entries by `name` slug before write; collision → fail-closed | future sprint |
| Compaction-survival test | `tests/test_compaction_substrate.py` mutates HOLISTIC.md + replays `/save` → asserts substrate restores cleanly | future sprint |

## Load-bearing constraints (LOCKED — every workstream reconciles all six)

| # | Constraint | Source |
|---|---|---|
| C1 | git-crypt blocks native `isolation: "worktree"` — every worktree creation MUST go via `bash lib/grip-worktree.sh add` (3-step: `--no-checkout` + symlink keys + checkout) | `rules/efficiency-rules.md` Rule 13 PARAMOUNT |
| C2 | Dispatch ambient floor ~84 000 tokens/agent — fan-out only when `useful_work ≥ 3 × floor (~252 000)`; below threshold use Shape B chain or Shape C main thread | `rules/dispatch-economics.md` |
| C3 | Exogenous anchor law — ≥1 edge evaluated by independent process; CI-on-mutation-surviving-tests / human-genesis / cross-provider verdict / pre-registered hypothesis. Self-grade forbidden. | `rules/exogenous-anchor-law.md` |
| C4 | Shared mutable state (`learning/insights.jsonl`, `cache/genome.json`, `data/genome-shared.json`, `embeddings.db`, `rules/HOLISTIC.md`) — single Shape B coordinator only; never parallel Shape A | `skills/bg-dispatch/SKILL.md` |
| C5 | Don't rebuild existing substrate — `grip-worktree.sh`, `bg_dispatch`, `hypothesis_engine`, `holistic_synthesiser`, `kono_preload`, `ci-review-driven-convergence`, `verify-canonical-not-claimed` are SHIPPED | (this ADR) |
| C6 | Self-verifying ≠ self-grading — any "verify" step uses an independent probe (live HTTP, cross-provider council, CI runner), never the same session that deployed | `rules/exogenous-anchor-law.md` + `rules/verify-canonical-not-claimed.md` |

## Manifest schema (LOCKED)

The manifest is the explicit contract — no implicit fan-out from a function argument list. Operators or upstream skills construct one, hand it to `validate_disjoint_ownership` + `select_shape`, then either dispatch (Shape A) or fall back (B/C).

```python
@dataclass(frozen=True)
class WorkstreamAtom:
    id: str                          # stable handle, e.g. "w2-a-foo"
    goal: str                        # one-line user-visible delta
    owns_paths: tuple[str, ...]      # files/dirs this atom may WRITE
    hypothesis_id: str | None = None # H-* id pre-registered before dispatch (C3)
    useful_work_tokens: int = 0      # estimate; gates Shape selection (C2)

@dataclass(frozen=True)
class WorkstreamManifest:
    sprint_id: str
    atoms: tuple[WorkstreamAtom, ...]
```

`owns_paths` is **writes-only**. Reads are unrestricted — substrate isolation is about writes, not reads.

## Shape selection (LOCKED)

```
select_shape(manifest):
    if any atom touches a shared-state prefix:   return "B"     # C4 wins
    if len(atoms) == 1:
        if atom.useful_work >= 3 × floor:        return "B"     # one agent worth it
        else:                                    return "C"     # main thread
    if every atom.useful_work >= 3 × floor:      return "A"     # fan-out admissible
    else:                                        return "B"     # mixed → chain
```

The function is mechanical — no LLM, no judgement call. Operator overrides go through a manifest amendment, not a `select_shape` override.

## Hypotheses

| H-id | Claim | Falsified if | Deadline |
|---|---|---|---|
| H-SWARM-MANIFEST-1 | The disjoint-ownership validator catches every C4 violation across ≥3 future sprint manifests | one violation reaches an Agent dispatch despite a manifest that passed validation | 2026-07-05 |
| H-SWARM-DISPATCH-ECON-1 | `select_shape()` matches the dispatch shape V>> (or the loop) would have picked manually on ≥70% of ≥10 future dispatches | operator overrides the gate on > 30% of dispatches | 2026-07-05 |

## Falsification (the ADR itself)

This ADR is wrong if:

1. The manifest schema lacks a field required by W5-D's `dispatch_fanout()` wiring — extend schema, register an addendum, don't silently bolt on.
2. The 84k floor proves wrong empirically (agents floor at a meaningfully different size) — recalibrate `DISPATCH_FLOOR_TOKENS`; preserve the ratio test.
3. `validate_disjoint_ownership` raises on legitimate shared-read patterns — split `owns_paths` into `writes_paths` + `reads_paths`.
4. Workstream 2's `min_signal_classes=2` blocks no real failure over ≥5 deploys — relax to 1 signal class + 1 hypothesis-pass.
5. Workstream 3's substrate writes via a Shape B coordinator measurably bottleneck legitimate progress over ≥10 substrate writes — investigate per-substrate-path partitioning (still single-writer per partition).

## Out of scope (explicit)

- Wiring `dispatch_fanout()` — that is W5-D of this sprint, gated on this ADR PR landing.
- Implementing `lib/deploy_verify_loop.py` — separate sprint, contract locked here.
- Implementing the Workstream 3 substrate (steering inbox, live-state manifest, duplicate guard, compaction-survival test) — separate sprint, spec locked here.
- Replacing `skills/bg-dispatch/SKILL.md` heuristics — this ADR is **complementary**, not a rewrite.

## References

- `drafts/ambitious-workflows-deliberation/00-grounding.md` — Phase 1 grounded synthesis (already-exists vs real-gap inventory)
- `plans/tender-popping-catmull.md` — overnight sprint plan (W2 atom contract)
- `rules/efficiency-rules.md` Rule 13 (PARAMOUNT) — worktree-first isolation
- `rules/dispatch-economics.md` (PARAMOUNT) — ~84k floor, ≥3× ratio
- `rules/exogenous-anchor-law.md` (PARAMOUNT) — C3
- `rules/ci-review-driven-convergence.md` (PARAMOUNT) — PR-loop doctrine
- `rules/verify-canonical-not-claimed.md` (PARAMOUNT) — C6
- `rules/syscall-doctrine.md` — sibling shim discipline (everything is a shim)
- `lib/grip-worktree.sh` — C1 canonical wrapper
- `lib/bg_dispatch.py` — reference pre-fetch primitive (composable with this)
- `skills/bg-dispatch/SKILL.md` — Shape A/B/C decision tree
- `lib/hypothesis_engine.py` — C3 mechanism
- `rules/knowledge-maturation-functor.md` — observation → memory → rule (N=1) → shim (N=3); this is the N=1 instance of "manifest-validated swarm coordinator"

Primary Author: V>>
