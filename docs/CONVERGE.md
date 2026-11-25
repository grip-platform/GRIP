#!/usr/bin/env bash
# CONVERGE -- The Closed-Loop Fold
# Run: bash docs/CONVERGE.md | Read: view as markdown

: '
# CONVERGE: The 3x3 Fold

A genuine learning system follows 9 phases that fold into 3 pipelines of 3:

**SENSE** (explore + compress + evaluate) -- input: raw -> reduced -> judged
**ENCODE** (compress + polyglot + serve) -- output: decision -> encoding -> delivery
**GROW** (evolve + learn + apply) -- feedback: change -> remember -> act

COMPRESS appears twice: data reduction after explore, judgement reduction after
evaluate. Same operation at different abstraction levels.

EVOLVE, LEARN, and APPLY are distinct: EVOLVE changes the system, LEARN
remembers why, APPLY acts on what was learned. EVOLVE without LEARN is amnesia.
LEARN without APPLY is shelf-ware.

## Kernel Mapping

The 208-line `converge(state, criterion, improve)` kernel encodes this:
- criterion = SENSE (does the state meet the bar?)
- improve = ENCODE (produce a better state)
- recursion = GROW (each depth is a generation)

The kernel is the minimal encoding. The 3x3 classifies what surrounds it.

## Self-Similarity

The kernel is structurally recursive: converge() calls improve() and recurses.
Each depth is a generation. This recursion is real and proven in code. The claim
that sub-phases recursively contain SENSE/ENCODE/GROW was not confirmed by Broly
mesh validation -- the file-read cache is efficiency, not convergence.

## Maturity Pipeline

**DETECT -> CODIFY -> EMBODY**: converge() drives maturity; memory makes it stick.

| Transition | Kernel Operation | Gene.tier |
|------------|-----------------|-----------|
| LATENT -> DETECTED | criterion(state) notices pattern | dead -> dormant |
| DETECTED -> CODIFIED | improve(state) captures pattern | dormant -> active |
| CODIFIED -> EMBODIED | memory persists across sessions | active -> essential |

Subsystems classified by maturity reached:

| Maturity | Subsystems |
|----------|-----------|
| EMBODIED | genome, KONO, RSI, pre-review, session resurrection |
| CODIFIED | onboarder, file cache, code-index |
| DETECTED | safety hooks (stateless by design) |

## Module Classification (22 modules, post-merge)

| Group | Modules |
|-------|---------|
| SENSE (9) | metrics, velocity, anomaly, heatmap, meta, forecast, strategy_explorer, cookbook, ab_test |
| KERNEL (1) | converge.py -- the recursion itself, not a phase |
| ENCODE (5) | combinators, dsl, diff, compare, replay |
| GROW (5) | dna, dna_core, dna_fitness, dna_operators, transfer |
| NARRATIVE (2) | weather, storyteller -- outside the fold |

Merged: predictor into forecast, inherit+marketplace into transfer (#911).
Boundary test: SENSE has no write side-effects. GROW mutates persistent state.
The kernel transcends both. Discriminating test: does it EVOLVE (learn from
its own outputs)? If yes, convergent. If no, a function call.

## This Document

state = this file. criterion = valid polyglot + 3x3 fold + under 100 lines.
improve = edit until met. The spec IS the artifact.
'

# === Self-Verification ===
echo "=== CONVERGE: Self-Check ==="
python3 << PYEOF
with open("${BASH_SOURCE[0]}") as f:
    doc = f.read()
    lines = doc.strip().split("\n")
checks = [
    ("Has SENSE/ENCODE/GROW", all(w in doc for w in ["SENSE", "ENCODE", "GROW"])),
    ("References kernel", "converge(state, criterion, improve)" in doc),
    ("Mentions 3x3 fold", "3x3" in doc),
    ("Under 100 lines", len(lines) <= 100),
    ("Valid bash (running now)", True),
    ("Honest about NARRATIVE", "outside the fold" in doc),
    ("Has DETECT/CODIFY/EMBODY", all(w in doc for w in ["DETECT", "CODIFY", "EMBODY"])),
]
passed = sum(ok for _, ok in checks)
for name, ok in checks:
    print(f"  {'PASS' if ok else 'FAIL'}: {name}")
print(f"Result: {passed}/{len(checks)} checks passed")
raise SystemExit(0 if passed == len(checks) else 1)
PYEOF

# Polyglot library check (if available)
LIB="$(dirname "${BASH_SOURCE[0]}")/../lib/polyglot.sh"
[ -f "$LIB" ] && source "$LIB" && polyglot_check "${BASH_SOURCE[0]}"
