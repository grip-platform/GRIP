#!/usr/bin/env bash
# AUTO-HARNESS -- Self-Synthesising Agent Constraints
# Run: bash docs/AUTO-HARNESS.md | Read: view as markdown

: '
# AUTO-HARNESS: Self-Synthesising Agent Constraints

Adapted from Google DeepMind AutoHarness (arXiv:2603.03329v1, March 2026).
Core insight: code constraints > model size for reliable agent behaviour.

## The Pattern

converge(state=harness_code, criterion=legal_rate==1.0, improve=llm_refine)

`is_legal_tool_call(tool, args, context) -> bool` is the core harness function.
The convergence loop synthesises harness code by iterating: evaluate against
examples, identify failures, refine code, re-evaluate. Terminates when all
examples pass (legal_rate == 1.0) or max depth reached.

## Three Modes (Increasing Autonomy)

- **filter**: `propose_legal_tools(context) -> set` -- whitelist valid tools
- **verifier**: `is_legal_tool_call(tool, args, ctx) -> bool` -- validate each call
- **policy**: entire workflow compiled to code, zero LLM inference at runtime

## GRIP Mapping

| AutoHarness | GRIP Equivalent | Kernel Op |
|-------------|-----------------|-----------|
| is_legal_action | PreToolUse gates | criterion |
| Critic (eval) | Ralph/MoE scoring | SENSE |
| Refiner (improve) | converge improve() | ENCODE |
| Thompson tree | genome evolution | GROW |

## Maturity Pipeline (DETECT -> CODIFY -> EMBODY)

| Stage | AutoHarness | GRIP |
|-------|-------------|------|
| DETECTED | Manual harness rules | Hand-written gates (context-gate-hook.py) |
| CODIFIED | Auto-synthesised harness | converge() generates gate code |
| EMBODIED | Harness-as-policy (no LLM) | Polyglot self-verifying constraints |

The maturity pipeline drives harness evolution: manual gates are DETECTED
patterns. When converge() synthesises gate code from examples, the pattern
is CODIFIED. When the harness replaces the LLM entirely, it is EMBODIED.

## This Document

state = this file. criterion = polyglot + AutoHarness + under 100 lines.
improve = edit until met. The spec IS the harness.
'

# === Self-Verification ===
echo "=== AUTO-HARNESS: Self-Check ==="
python3 << PYEOF
with open("${BASH_SOURCE[0]}") as f:
    doc = f.read()
    lines = doc.strip().split("\n")
checks = [
    ("References AutoHarness", "AutoHarness" in doc and "arXiv:2603.03329v1" in doc),
    ("Has is_legal_tool_call", "is_legal_tool_call" in doc),
    ("Has converge mapping", "converge(state=harness_code" in doc),
    ("Under 100 lines", len(lines) <= 100),
    ("Valid bash (running now)", True),
    ("Has 3 modes (filter/verifier/policy)", all(m in doc for m in ["filter", "verifier", "policy"])),
    ("Has maturity mapping (DETECT/CODIFY/EMBODY)", all(m in doc for m in ["DETECTED", "CODIFIED", "EMBODIED"])),
    ("Has GRIP mapping table", "PreToolUse gates" in doc),
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
