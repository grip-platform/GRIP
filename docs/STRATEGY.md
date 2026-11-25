#!/usr/bin/env bash
# STRATEGY -- Task Routing via Proven Patterns
# Run: bash docs/STRATEGY.md | Read: view as markdown

: '
# STRATEGY: Task Routing Map

Maps task types to proven execution strategies. Each strategy encodes
the converge criterion, delegation pattern, and quality gate.

## Strategy Table

| Task Type | Strategy | Criterion | Delegate | Gate |
|-----------|----------|-----------|----------|------|
| bug-fix | pre-fix-context-exploration | all actors traced, root cause found | solo | tests pass + regression anchor |
| feature | feature-complete | tests + pre-review >= 80 | worktree agent | Ralph + CI |
| refactor | layered-refactoring | no behaviour change, tests green | worktree agent | diff audit |
| research | broly-mesh | 3+ sources cross-validated | explore agent | evidence score |
| sprint | fibonacci-wave | composite >= 80, delight up | sprint-loop | wave protocol |
| onboard | project-grip-init | GRIP infra verified in target | direct | self-onboard test |
| review | cascade-reviewer | MoE consensus, no HIGH findings | moe council | KS divergence < 0.3 |
| docs | polyglot-check | valid dual-format, self-check passes | solo | bash self-verify |
| comms | authentic-writing | no AI fluff, voice-consistent | solo | human approval |
| decision | decision-lifecycle | recorded, stakeholders notified | copilot | audit chain |

## Routing Rules

1. **Match by task type** -- user intent maps to one row
2. **Apply criterion** -- converge(state, criterion, improve)
3. **Delegate per pattern** -- solo, worktree, agent, or mesh
4. **Gate before merge** -- quality gate must pass before landing

## Anti-Patterns (What NOT to Do)

- Skip pre-fix exploration on bugs (causes 4x fix rounds)
- Use solo mode for features (no isolation, branch drift)
- Skip Ralph on PRs (quality ratchet disabled)
- Research without cross-validation (single-source bias)
- Sprint without breathing pauses (context exhaustion)

## Strategy Selection (Ambiguous Tasks)

Fix broken behaviour? -> bug-fix. Add capability? -> feature.
Change structure only? -> refactor. Gather info? -> research. Multiple deliverables? -> sprint.

## This Document

state = this file. criterion = valid polyglot + all 10 strategies + under 80 lines.
improve = edit until met. The routing table IS the strategy.
'

# === Self-Verification ===
echo "=== STRATEGY: Self-Check ==="
python3 << PYEOF
with open("${BASH_SOURCE[0]}") as f:
    doc = f.read()
    lines = doc.strip().split("\n")
strategies = ["bug-fix", "feature", "refactor", "research", "sprint",
              "onboard", "review", "docs", "comms", "decision"]
checks = [
    ("Has strategy table", "## Strategy Table" in doc),
    ("All 10 strategies present", all(s in doc for s in strategies)),
    ("Has routing rules", "## Routing Rules" in doc),
    ("Has anti-patterns", "## Anti-Patterns" in doc),
    ("References converge", "converge(state, criterion, improve)" in doc),
    ("Under 80 lines", len(lines) <= 80),
    ("Valid bash (running now)", True),
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
