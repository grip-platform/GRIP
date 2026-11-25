# GRIP as a Structural Mapping Engine — Phase A Dogfood Audit

Faithfulness analysis of GRIP's implementation of Gentner's
Structure-Mapping Theory (1983), produced by dogfooding GRIP's own SMT
enforcer/analyser on GRIP itself (converge-fleet, 6 audit lanes, 2026-09-02).

## Executive verdict

GRIP has an SMT **gate**, not an SMT **engine**. The gate validates
hand-authored proposals by trusting self-declared relation labels; it
cannot represent, verify, or construct any of the structural relations
Gentner's theory is built on. In production it blocks nothing: zero
blocking call-sites, and the one live consumer can mathematically never
deny a non-empty claim and fails open on error. SME (mapping
construction), MAC/FAC (structural retrieval), the Relational Shift and
Comparison in Learning are absent or shell-only. Faithfulness to SMT:
**low on every load-bearing construct** (see table). This document is
the Phase A deliverable and the gap list Phase B implements against;
Phase C re-runs this audit until no open gap remains.

## 1. Fidelity: theory → code (Lane A1)

| Construct | Status | Fidelity | Evidence |
|---|---|---|---|
| Entities as nodes | PARTIAL | 40 | `source_elements`/`target_elements` are flat strings; nothing attaches to nodes |
| Attributes vs relations | ABSENT | 5 | `MappingRelation.relation` ∈ {causal, orthogonal, surface, meta} is a label on the mapping, not a predicate arity on domain elements |
| Higher-order relations | ABSENT | 0 | No predicate hierarchy; nothing weighs higher-order matches |
| Relational focus | PARTIAL | 35 | 60%-causal / 30%-surface ratios over **self-declared** labels |
| 1-1 correspondence | PARTIAL | 20 | many-to-one only, and only at >3; one-to-many never checked; a legitimate 100% complete map FAILS the over-mapping band (anti-Gentner) |
| Parallel connectivity | ABSENT | 0 | No predicate-argument structure exists to align |
| Systematicity | ABSENT | 10 | Replaced by a 30-70% coverage "ideal"; no connectivity measure |
| Candidate inference | ABSENT (inverted) | 5 | Unmapped base structure is penalised as "orthogonal leftovers"; SMT projects it |
| Similarity space | PARTIAL | 20 | Binary surface-vs-causal; no literal-similarity/analogy/anomaly classes |
| Tiered identicality | ABSENT | 0 | No predicate identity tiering anywhere |

## 2. SME stages (Lane A2)

| Stage | Verdict |
|---|---|
| 1. Local match construction | ABSENT |
| 2. GMap greedy merge | ABSENT |
| 3. Structural evaluation | PARTIAL — scores hand-authored `MappingProposal`s only; never computes size × consistency × systematicity from predicate data |
| 4. Candidate inference | ABSENT — `unmapped_source` is checked for orthogonality, never projected |

Nearest dormant machinery (verify-round find, folded in): `lib/inception_limbo.py`
implements a relations-vs-entities split (`relational_signature()`) and a
relational-overlap vs surface-overlap convergence detector
(`find_convergences()`) — the closest thing to predicate structure in the
codebase — with **zero production callers** (test-only).

## 3. MAC/FAC (Lane A3)

MAC half EXISTS (sqlite-vec KNN + FTS5 + RRF fusion + recency decay —
`lib/embeddings/search.py`, `lib/kono_substrate.py`). FAC half ABSENT: every
retrieval pipeline terminates at a scalar score sort; `gentner_enforcer` is
never fed retrieval candidates. Natural integration point:
`kono_substrate._finalize_results` (top-N → structural re-rank before
attestation) — since wired, see G5 in the gap table below.

## 4. Relational shift + comparison in learning (Lane A4)

- Relational shift: PARTIAL — `/evo`'s raw→calibrated trajectory tracks
  judgement evolution, but on a false-flag axis, never attribute→relational.
- Progressive alignment: PARTIAL — all abstraction is frequency/Jaccard
  aggregation (min_size=3, N=3, gene co-occurrence); no pairwise structural
  alignment before abstraction.
- Alignable differences: PARTIAL — concept live only as a gate on claimed
  analogies; no learning loop computes differences between two aligned
  structures. `lib/backlog_gates.py` `gentner_connection_gate` is dormant
  (test-only).
- The 3x/YSH maturation ladder is a well-typed monotone enforcement functor
  with a systematicity-flavoured N-gate and **no computed comparison** —
  the shell of progressive alignment with the alignment step outsourced to
  agent judgement.
- Integration points: `learning_detector.consolidate_insights`
  (replace Jaccard adjacency with structural pairing + alignable-difference
  rationale); wire the dormant `gentner_connection_gate` into backlog REASON.

## 5. Dogfood mechanical evidence (Lane A5)

`gentner_enforcer.enforce()` run on GRIP's own real mapping claims:

| Proposal | total | decision | Note |
|---|---|---|---|
| wave-protocol ↔ Fibonacci (docstring claim) | 100.0 | allow | |
| dual-zero ↔ Śūnyatā (EvidenceState paper) | 88.8 | allow | the wrong-as-engineering surface claim cost only 11.2 pts |
| mode-to-wave unification (real `_HARDCODED_WAVE_MAP`) | 100.0 | allow | |
| negative control: pure attribute analogy, sloppy bookkeeping | 39.0 | deny | needed 3 failing checks to clear <40 |
| same pure-attribute proposal, clean bookkeeping | 41.0 | deny as "significant structural issues" | the "surface-level" band (<40) is unreachable with honest bookkeeping |
| **probe: YELLOW(sun)→YELLOW(orange) labelled causal, fabricated justification** | **86.0** | **allow** | **attribute-as-relation passes; the two checks that exist to catch it scored 100** |

## 6. Consumption audit (Lane A6)

- Blocking call-sites in production: **0**. `enforce_external` and
  `decision_field_gate` are dead (test-only).
- The one live consumer (`lib/gentner_gate_cli.py:45-46,66-75`, used by the
  grip-channel bridge) builds a single `relation="causal"`,
  `justification=claim` relation — any non-empty claim scores 86/100
  unconditionally — and fails open to `allow` on any exception.
- `lib/wave_enforcer.py` claims "using gentner_enforcer" in its docstring
  but never imports or calls it.
- No hooks, no CI wiring. `rsi/optimal.py`'s scorer has zero callers and
  swallows exceptions to 0.0.

## 7. Ranked gap list (Phase B implementation order)

1. **Predicate representation** — entities + attributes vs relations +
   arity + higher-order relations; the substrate every other fix needs.
   (Revive/absorb the dormant `inception_limbo.py` relation-entity split
   rather than writing from scratch.)
2. **Structural consistency checks** — true 1-1 (both directions, any
   duplication), parallel connectivity, systematicity (connectedness,
   not coverage); replace the 30-70% band.
3. **SME stages 1-2** — local match construction (depth-weighted) +
   GMap greedy merge; make GRIP able to CONSTRUCT mappings.
4. **SME stage 4** — candidate inference projection (unmapped base
   structure becomes proposals, not suspicions).
5. **MAC/FAC** — wire the enforcer as FAC re-ranker after KONO's MAC
   sweep at `_finalize_results`.
6. **Progressive alignment** — structural pairing in
   `consolidate_insights` + activate `gentner_connection_gate`.
7. **Production enforcement** — make at least one path blocking, close
   the bridge fail-open, fix `wave_enforcer.py`'s docstring, CI wiring.
8. **Lean formalisation** — `formal/SMT.lean`: 1-1 + parallel
   connectivity as theorems, systematicity as a scoring monotonicity
   theorem, SME stage invariants, sorry-free.

## 8. Falsification of this audit

This audit is wrong if any lane claim above is refuted against the code
by the adversarial verify round (below), or if a listed ABSENT construct
is found implemented under a name the search vocabulary missed.

**Status: partially triggered.** The verify round found one such miss —
`lib/inception_limbo.py`'s dormant relational-signature machinery (see §2) —
which has been folded into this document. The remaining claims survived
refutation 13/13 (§1, 2, 5, 6) with zero refutations (§3, 4, 7).

---

*Phase A, 2026-09-02. Lanes A1-A6 run as background agents; verify round
pending. Produced under V>>'s direction; orchestrated by DeepSeek V4 Pro.*

---

## Phase B — kernel shipped (2026-09-06, AFK wave)

Implemented against the ranked gap list, verified by `tests/test_smt_kernel.py`
(21 tests), `tests/test_smt_text_structure.py` (7 tests), `tests/test_smt_wiring.py`
(13 tests) and `formal/SMT.lean` (lean 4.33.1, sorry-free, verified):

| Gap | Deliverable | Status |
|-----|-------------|--------|
| G1 predicate representation | `lib/smt/sme.py` — Entity/Predicate/Structure; attributes (arity 1) vs relations (arity ≥ 2) vs higher-order (predicate args); tiered identicality (identical > synonymous) | SHIPPED |
| G2 structural consistency | 1-1 both directions, parallel connectivity (args must map), systematicity as largest consistent connected substructure | SHIPPED |
| G3 SME stages 1-3 | local match construction (name identity + shared-argument-slot entity links — the solar↔atom analogy works), GMap greedy merge with revisit passes, structural evaluation | SHIPPED |
| G4 candidate inference | `candidate_inferences()` projects unmapped base structure with mapped args — proposals, not suspicions | SHIPPED |
| G5 MAC/FAC | `lib/smt/macfac.py` — FAC structural re-ranker + fusion, wired into `kono_substrate._finalize_results` behind `GRIP_SMT_FAC` (default on). Re-ranks the top window of MAC hits when the query itself states a relation; `score` keeps its MAC value so the `min_score` contract holds; fails open to MAC order | SHIPPED, WIRED |
| G6 progressive alignment | `lib/smt/relational_shift.py` — alignable differences (base_only/target_only/role_flip/tier_flip), progressive alignment with min_support, relational-shift measure, wired into `learning_detector.consolidate_insights` behind `GRIP_SMT_RELATIONAL_SHIFT` (default on). Literal pairing merges restatements Jaccard misses and refuses role flips Jaccard accepts; each merge records its shared relations and shift; falls back to Jaccard on failure | SHIPPED, WIRED |
| G7 production enforcement | blocking call-sites, bridge fail-open closure, wave_enforcer docstring — tracked as issue #5364, deliberately separate from the wiring because it touches paths that can stop work | OPEN |
| G9 text bridge | `lib/smt/text_structure.py` — deterministic free text → Structure (fixed connective and modifier lexicon, no learned model). The kernel reasons over predicates; GRIP stores prose. Added while wiring G5/G6; without it the kernel had no way to reach a live corpus | SHIPPED |
| G8 Lean formalisation | `formal/SMT.lean` — consistent_append, merge_add_consistent, filter_length_le, tail_length_le, size_of_cons_gt | SHIPPED, VERIFIED |

Phase A falsification probe re-run on the kernel: the YELLOW(sun)→YELLOW(orange)
attribute-as-causal-relation claim is now structurally inadmissible — attributes
match only at attribute tier (weight 1.0), never as relations.

Phase C criterion (zero open gaps) is still NOT met: G5 and G6 are now wired
into their production seams and proved there by behaviour tests, but G7
(production enforcement) remains open as issue #5364. Honest status, not a
faked completion.

Two residuals from the wiring, recorded rather than hidden. Predicate names are
unique within a Structure, so a repeated relation in one text is named `CAUSES_2`
and pairs by ordinal across texts rather than by meaning. And `text_structure.py`
is a lexicon, not a parser: it will miss relations phrased outside its
connective list, which is acceptable for a re-ranker that fails open and is the
reason G7 should not gate on it until its extraction accuracy is measured.
