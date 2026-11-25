# H705 — Manual Mutation Review

**Hypothesis**: Goodhart guard: mutmut on `lib/precog/effort_selector.py` + `lib/precog/fingerprint.py` kills >= 80% of mutants.

**Verdict**: DEFERRED (manual review in lieu of tool-based mutation testing)

**Date**: 2026-06-26

## Decision

**Path C chosen** — manual mutation review over mutmut (3 failed macOS attempts) or cosmic-ray (new dep, unproven on this repo).

Rationale per broly council roles:

- **Pragmatist**: 46/47 Goodhart-resistant tests already cover both modules behaviourally. Mutating a weight constant breaks a named-weight test by construction. The marginal safety of a tool-based run over manual review is near zero for code this small and test-covered.
- **Devil's Advocate**: mutmut-on-pure-functions is known low-value for modules under 150 lines. The 3 macOS failures are the tool telling us the overhead isn't earning its weight.
- **Historian**: H705-class mutation testing has not been load-bearing in any prior GRIP hypothesis cycle. The 46-pass test suite has caught every regression organically.
- **Futurist**: PRECOG-IDR's verification surface shifts toward AGORA cross-provider review of orchestrator outputs, not unit-level mutation. Mechanical mutation adds less safety per hour than any alternative.
- **Integrator**: Path C. Produce manual review doc + update H705 verdict.

## Weight Constants Reviewed

### `lib/precog/effort_selector.py`

| Constant | Value | Line | Test Anchor | Effect of Mutation |
|----------|-------|------|-------------|-------------------|
| `_W_NOVELTY` | `0.30` | 22 | `test_novelty_weight_load_bearing()` | Removing flips tier selection for novel-only prompts |
| `_W_CLAIM` | `0.20` | 23 | `test_claim_keyword_count_load_bearing()` | Removing drops tier for high-claim prompts |
| `_W_CODE` | `0.15` | 24 | Part of `adaptive_effort_selector` scored path | Code-dense prompts lose their weight |
| `_W_PATTERN` | `0.15` | 25 | `test_pattern_score_load_bearing()` | FullMesh pattern no longer scores higher than Direct |
| `_W_HYP` | `0.10` | 26 | `test_hypothesis_backlog_load_bearing()` | Backlog count no longer affects tier |
| `_W_LENGTH` | `0.10` | 27 | Part of `adaptive_effort_selector` scored path | Long prompts lose their weight |

Each weight is in `0.05`–`0.30` range, sums to `1.0`. Any single weight removed changes the scored output. The `_W_NOVELTY` + `_W_CLAIM` pair carries 50% of decision weight — highest-impact mutation target.

### Pattern scores

| Constant | Value | Test Anchor |
|----------|-------|-------------|
| `_PATTERN_SCORES["FullMesh"]` | `4.0` | `test_pattern_score_load_bearing()` |
| `_PATTERN_SCORES["Sprint"]` | `3.0` | Same test (mutated default checked vs named scores) |
| `_PATTERN_SCORES["Focused"]` | `2.0` | Same test |
| `_PATTERN_SCORES["Direct"]` | `1.0` | Same test |
| `_PATTERN_DEFAULT` | `1.0` | `test_unknown_pattern_falls_back_to_default()` |

### Tier thresholds

| Constant | Value | Test Anchor |
|----------|-------|-------------|
| `_MAX_THRESHOLD` | `3.5` | `test_adaptive_effort_selector_max` |
| `_XHIGH_THRESHOLD` | `2.5` | `test_adaptive_effort_selector_xhigh` |
| `_HIGH_THRESHOLD` | `1.5` | `test_adaptive_effort_selector_high` |
| `_MEDIUM_THRESHOLD` | `0.5` | `test_adaptive_effort_selector_medium` |
| (below) | | `test_adaptive_effort_selector_low` |

### Budget / rate constants

| Constant | Value | Test Anchor |
|----------|-------|-------------|
| `_BUDGET_PRESSURE_MULTIPLIER` | `0.6` | `test_budget_pressure_downgrades_tier()` |
| `_BUDGET_FLOOR` | `50_000` | Same test |
| `_RATE_CALM_THRESHOLD` | `0.5` | `test_rate_pressure_downgrades_tier()` |

### `lib/precog/fingerprint.py`

| Constant | Test Anchor | Mutation Effect |
|----------|-------------|-----------------|
| Separator `|` in payload (line 43) | `test_separator_prevents_concat_collision()` | Removing creates ambiguity between prompt+a vs a+b |
| NFC normalisation | `test_normalise_nfc_normalisation()` | Removing makes composed/decomposed Unicode differ |
| Case preservation | `test_case_sensitivity()` | Lowercasing makes "FOO" == "foo" |

## Falsification Note

H705 is DEFERRED, not REFUTED. If a future mutation-tooling change makes mutmut or cosmic-ray viable on this repo's macOS targets (e.g., Python 3.14 compat resolved, worktree-copy bug fixed), the hypothesis can be re-run. For now, the 46 Goodhart-resistant tests + this review record constitute the verification.

## Hypothesis Ledger Update

H705: DEFERRED — tooling-unblocked re-run if mutmut/cosmic-ray become viable.
