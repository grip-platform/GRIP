# Sunyata EvidenceState Pilot — Results

Run: 2026-08-30 (pilot build + first full run). Pre-registered hypotheses
H714–H716, deadline 2026-10-31, bound to PR #5019. Results machine-readable
at `lib/sunyata_pilot/results.json`.

## Verdicts

| Hypothesis | Verdict | One-line reason |
|---|---|---|
| H1 (H714) — selective correctness at matched coverage | **FALSIFIED** | novelty collapses to baseline: B1+CONFLICT (Belnap 1977 prior art) achieves the full fabrication win (0 vs G0's 34); G1 differs from B2 on the H1 axis only by the value-free `file_under_assumption` knob |
| H2 (H715) — audit efficiency and accuracy | **FALSIFIED** | no step improvement against the conventional-log baseline at full accuracy (fold-C4 pre-named risk: ledger clutter). Accuracy held: G1 1.0 vs B1 0.879 — but steps did not drop |
| H3 (H716) — the lens must earn its name | **CONFIRMED** | both declared candidates show benchmark deltas: (i) the CONFLICT corner drives different actions than LATENT (B1 12 fabrications vs B2 0 on conflicting vars); (ii) the state-cycles/ledger-monotone split changes multi-session behaviour (G1 cites evicted flips via `file_under_assumption`; B1's conventional log carries no eviction trace) |

## Raw numbers (24 scenarios, 99 var-instances, coverage floor 0.60)

| System | Fabrications | Coverage |
|---|---|---|
| G0 (ungated) | 34 | 1.00 |
| B1 (Kleene + options + audit log) | 12 | 0.81 |
| B2 (B1 + CONFLICT) | 0 | 0.66 |
| G1 (full EvidenceState gate) | 0 | 0.66 (asserted) / 0.84 (incl. file_under_assumption) |

## Adversarial verify round (survived refutation)

Two independent refuters attempted to overturn the verdicts via harness
refinements proposed by the build thread. Both refinements were REFUTED:

- H1 "behavioural-profile match" refinement — post-hoc, gameable: a cheating
  G1 emitting value-less asserts scores fab 0 at coverage 1.0 and escapes
  the refined check; the refinement cannot distinguish it from the honest G1.
- H2 "index/np skip credit" refinement — exploitable: the
  `negative-provenance:` line is syntactically checked but never
  cross-verified against entries; a lying np line is absorbed by the auditor
  and accuracy scoring is out-of-remit blind to wrong values on resolvable
  vars. The per-line charging model is the only coupling between cost and
  verification.

Verdicts above are the pre-registered falsifiers applied as written.

## Recorded instrument defects (for the next pilot, pre-register before running)

1. **H1 degeneracy.** Because B2 sits at the fabrication floor, the
   novelty-collapse falsifier fires iff `G1 fabrications == 0` — the ideal
   outcome falsifies H1 by construction. A non-degenerate revision must be
   pre-registered (e.g. novelty judged by H3 alone, or by a
   mechanism-fingerprint metric), never retro-fitted to this run.
2. **G0 transcript terse.** G0's event artefact is one clean line per var,
   not the verbose prose transcript the paper means; H2's G0 leg is
   decorative anyway (the hardened §8 falsifier compares G1 vs B1 only).
3. **H2 accuracy blind spot.** Wrong asserted values on resolvable vars are
   outside H2's remit by design; a future audit-efficiency instrument
   should also count value-correctness, or state why not.

## What survives as true

- The CONFLICT corner is load-bearing and cheap (B2: 12 → 0 fabrications
  for one bit-pair corner). H3(i).
- The sealed flip ledger changes multi-session behaviour where a
  conventional log is silent. H3(ii).
- G1's negative provenance and flip sealing remain the *regulatory
  artefact* claims (§10.1) — H2 measured audit time, not regulatory value.
- The paper's own commitment holds: the loser is printed. At pilot scale,
  on the fabrication axis, Belnap 1977 + Kleene + options suffice; the
  Śūnyatā lens earns its keep as the *forcing of both corners into one
  artefact*, not as a fabrication-beating mechanism.

## Provenance

Built by a converge-fleet (5 parallel lanes, 2 barrier rounds, 2
adversarial verifiers) under V>>'s direction. Lane code:
`lib/sunyata_pilot/` (common.py, benchmark/, agents/{g0,g1,b1,b2}.py,
harness/{h1,h2,h3}.py); runner `bin/sunyata-pilot`; tests
`tests/test_sunyata_pilot_*.py` (all green).
