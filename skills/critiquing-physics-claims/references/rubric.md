# Scoring Rubric: The Six Axes

Each axis is scored 0-10 where **higher means more problematic**. The final bullshit meter score is computed from axis scores via the rule at the bottom. Fatal flaws on testability or rigour dominate — good scores elsewhere cannot compensate.

---

## Axis 1: Testability (weight: high)

| Score | Meaning |
|-------|---------|
| 0-2 | Makes quantitative predictions that differ from existing theory in a measurable regime, with specified signal-to-noise and null hypothesis |
| 3-4 | Makes qualitative predictions checkable with current technology; magnitude vague |
| 5-6 | "Consistent with" existing data but makes no new predictions |
| 7-8 | Predictions exist but are unreachable in principle (too far, too small, too early universe) |
| 9-10 | Nothing could falsify this — pure metaphysics in equation form |

**Diagnostic question**: "Describe the experimental result that would make the author abandon the theory." If the author cannot answer, score 8+.

---

## Axis 2: Mathematical Rigour (weight: high)

| Score | Meaning |
|-------|---------|
| 0-2 | Full Lagrangian / Hamiltonian / action present; derivations work; dimensional analysis checks out; limits match known physics |
| 3-4 | Core equations present but gaps in derivation; dimensional analysis not shown |
| 5-6 | Suggestive equations without clear dynamics; no equations of motion |
| 7-8 | Analogies and word-equations; no formalism one could compute with |
| 9-10 | Math-shaped text with no computable content; symbols used for decoration |

**Diagnostic question**: "Given this formalism, compute one numerical prediction." If impossible, score 7+.

---

## Axis 3: Novelty (weight: medium)

| Score | Meaning |
|-------|---------|
| 0-2 | Genuinely new mechanism, prediction, or mathematical structure |
| 3-4 | New combination of known elements; the combination itself is non-trivial |
| 5-6 | Old wine in new bottles, but the bottle has something interesting |
| 7-8 | Rebranding — strip the vocabulary and it is [well-known existing framework] |
| 9-10 | Rebranding that the author does not seem to realise is rebranding |

**Diagnostic question**: "What is the closest existing framework? What does this add that framework lacks?" If nothing, score 8+.

---

## Axis 4: Precision of Definitions (weight: medium)

| Score | Meaning |
|-------|---------|
| 0-2 | Every key term has one definition, used consistently throughout |
| 3-4 | Most terms defined; minor drift in peripheral concepts |
| 5-6 | Central terms shift meaning between sections |
| 7-8 | Key terms undefined or circularly defined |
| 9-10 | Terminology used for rhetorical effect, not for meaning |

**Diagnostic question**: "Pick the three most important terms. Does each have one precise definition?" If shifting, score 6+.

---

## Axis 5: Scope Honesty (weight: medium)

| Score | Meaning |
|-------|---------|
| 0-2 | Abstract matches delivery. Modest claims, modest derivation. |
| 3-4 | Slight inflation — one or two claims overreach |
| 5-6 | Moderate inflation — abstract promises more than body delivers |
| 7-8 | Heavy inflation — "theory of X" built on a single toy model |
| 9-10 | "Theory of everything" from a napkin sketch |

**Diagnostic question**: "Does what the abstract promises match what the conclusions can support?" If no, score by the gap size.

---

## Axis 6: Red Flags (weight: variable)

Count flags from `persona.md` red flag checklist. Each standard flag contributes 1 point; severe flags (teleology-as-dynamics, unfalsifiable-as-prediction) contribute 2. Cap at 10.

---

## Final Score Rule

```
rigour_floor = max(axis_1, axis_2)
soft_inflation = 0.5 * max(axis_3, axis_4, axis_5)
flag_contribution = min(axis_6, 4)

raw_score = rigour_floor + soft_inflation + flag_contribution - 2
final = clamp(round(raw_score), 0, 10)

# Fatal-flaw override
if axis_1 >= 9 or axis_2 >= 9:
    final = max(final, 8)
```

The `-2` offset prevents the rubric from punishing every paper that is not a Nobel candidate — most published physics scores in the 2-4 range, which is the "promising but needs work" band.

The fatal-flaw override enforces: a paper with nothing to falsify, or no math to compute, scores at least 8 regardless of how pretty the prose is.

---

## Worked Calibration Examples

| Paper archetype | Axis 1 | Axis 2 | Axis 3 | Axis 4 | Axis 5 | Axis 6 | Final |
|-----------------|--------|--------|--------|--------|--------|--------|-------|
| Standard Model calculation | 1 | 1 | 2 | 1 | 1 | 0 | 0 |
| New BSM model with predictions | 3 | 2 | 3 | 2 | 3 | 1 | 2-3 |
| String theory landscape paper | 7 | 3 | 5 | 4 | 6 | 2 | 6-7 |
| Consciousness + quantum proposal | 8 | 6 | 8 | 7 | 7 | 4 | 8-9 |
| Pure philosophy with symbols | 10 | 9 | 9 | 8 | 9 | 5 | 10 |

These calibrate the reviewer. A score of 5 is genuinely mid-range, not "bad". A score of 8 means "this needs to stop pretending to be physics". A score of 10 is rare and should require explicit justification.
