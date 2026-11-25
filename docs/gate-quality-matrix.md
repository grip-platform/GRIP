# GRIP Quality Gate Matrix

Mechanical enforcement specification for the PreToolUse / PostToolUse / commit-time gates that implement Rule 18 (JIT Quality — Correct-by-Construction).

This doc is the single source of truth for which principles are blocked, warned, or audit-logged, and for the auto-refactor pipeline that backs the Correct-by-Construction loop.

## Three enforcement tiers

| Tier | Behaviour | When appropriate |
|------|-----------|------------------|
| **BLOCK** | PreToolUse hook returns non-zero; tool call refused; model sees violation + suggested auto-refactor diff | Principle is mechanically detectable with near-zero false positives and the fix is deterministic |
| **WARN** | Violation emitted to context as `[QUALITY]` signal; model addresses before next task (Rule 15) | Principle requires judgement about intent; mechanical detection has false positives |
| **AUDIT-LOG** | Violation recorded to `logs/quality-audit.jsonl` for trend analysis; no immediate feedback | Principle is expensive to detect or too subjective for real-time action |

## Classic 8 principles — enforcement tier

| Principle | Tier | Detection | Notes |
|-----------|------|-----------|-------|
| **DRY** (Don't Repeat Yourself) | BLOCK | Token-sequence similarity >0.7 within/across files | Existing hook; auto-refactor emits extracted helper |
| **KISS** (Keep It Simple, Stupid) | BLOCK | Function >30 lines OR nesting depth >4 | Existing hook; auto-refactor suggests decomposition |
| **SOLID-SRP** (Single Responsibility) | BLOCK | Class >500 lines OR >30 methods OR >5 distinct responsibility domains (method-prefix clustering) | Quantifiable; auto-refactor suggests class split |
| **SOLID-OCP** (Open/Closed) | WARN | New `isinstance()` / `type() ==` chains on types that already have dispatch tables | Intent-sensitive; false positives real |
| **SOLID-LSP** (Liskov Substitution) | WARN | Subclass overrides strengthen precondition / weaken postcondition | mypy + contract inference catches most |
| **SOLID-ISP** (Interface Segregation) | BLOCK | ABC or Protocol with >5 abstract methods | Mechanical; auto-refactor suggests interface split |
| **SOLID-DIP** (Dependency Inversion) | WARN | Concrete-class imports in high-level modules (top-level `lib/` importing specific DB drivers) | Architecture-sensitive |
| **GRASP** (9 sub-principles) | WARN | Information Expert / Creator / Low Coupling / High Cohesion violations via dependency graph analysis | Shape-sensitive; good for advisory |
| **YAGNI** (You Aren't Gonna Need It) | AUDIT-LOG | New abstractions with only one concrete implementation + no test of variation | Subjective; log and review |
| **YSH** (You Should Have) | WARN | Pattern duplicated ≥3 times across changed files triggers "extract now" suggestion | History-sensitive; needs git log |
| **CC** (Cyclomatic Complexity) | BLOCK | `lib/complexity_profiler.calculate_complexity()` > threshold per `rules/cc-enforcement.md` | Existing; trust-level-modulated (STRICT/DEFAULT/RELAXED) |
| **BIG-O** (Algorithmic Complexity) | WARN | Nested loops over same collection / `list.index()` inside loop / recursive fn without memoisation | Shape-based; false positives exist |

## New additions — principles worth enforcing

Beyond the classic 8, these additions close high-value gaps:

| Principle | Tier | Detection | Auto-refactor | Why |
|-----------|------|-----------|---------------|-----|
| **Law of Demeter** | BLOCK | AST: attribute/method chain length > 2 (`a.b().c().d()`) outside builder patterns | Extract intermediate variable; suggest delegate method on `a` | Biggest underappreciated coupling reducer; trivially mechanical |
| **Cognitive Complexity** | BLOCK | Sonarqube metric: nesting + binary logical ops + control flow breaks (not CC). Threshold 15 warn / 25 block | Extract nested conditions into named predicates | CC counts *paths*; CogC counts *understandability*. Catches nested comprehensions, chained ternaries, multi-clause ifs |
| **Long Parameter List** | BLOCK | Function signature with >4 positional args | Group into `@dataclass` | Auto-refactor pattern is deterministic |
| **Magic Numbers** | WARN | Non-`{-1, 0, 1, 2}` numeric literals outside tests/config | Extract to `_NAMED_CONSTANT` | Cheap signal; low friction |
| **Dead Code** | BLOCK | `ruff F401`, `F841`, unreachable | Delete | Free cleanup; no judgement needed |
| **Test-Existence Floor** | BLOCK | Every new public function must have ≥1 test reference within 2 waves of creation | Emit test scaffold | Complements Rule 14 (Goodhart); Rule 14 covers test *quality*, this covers test *presence* |
| **Nested Conditional Depth** | BLOCK | If/else pyramid depth >3 | Extract early-return guards | Shape-focused; distinct from CC |
| **Connascence Ratchet** | AUDIT-LOG (per file) | Count connascence-of-position + meaning + algorithm per file | Monotonic-decrease gate at commit time | Formal coupling metric. Feeds the Broken Windows meta-gate |

## Broken Windows meta-ratchet

The gate-of-gates. Makes every principle above ratcheted even when individual checks only WARN.

### Rule

Per file, total violation count in changed files at commit time cannot exceed the pre-edit count.

### Implementation (pseudo-code)

```python
# hook: commit-msg OR pre-push
changed_files = git_diff_names("HEAD~1", "HEAD")
for path in changed_files:
    pre_count  = count_violations_at_commit(path, "HEAD~1")
    post_count = count_violations_at_commit(path, "HEAD")
    if post_count > pre_count:
        reject(f"Broken Windows: {path} grew from {pre_count} to {post_count} violations")
```

### Why this is the single highest-leverage gate

- **Monotonic improvement** — codebase quality can only go up (or stay flat) per file
- **No blanket enforcement required** — principles that WARN still ratchet; the gate doesn't need them to BLOCK
- **Graceful legacy** — existing violations aren't forced out overnight; they just can't grow
- **Opt-in by edit** — you only face the ratchet on files you touch

### Baseline calibration (one-time)

On first activation, a baseline audit establishes per-file violation counts. The ratchet compares against this baseline until stability is confirmed, then switches to per-commit diffing.

## Auto-refactor pipeline (the JIT loop)

Three phases per edit, driven by the gate suite:

```
PreToolUse   ─┐
              │ detect violations in proposed edit
              │ if BLOCK tier: reject with suggested auto-refactor diff
              │ if WARN tier: annotate context as [QUALITY] signal
              │ if AUDIT-LOG: append to logs/quality-audit.jsonl
              ▼
Model rewrite (Rule 18: Correct-by-Construction)
              │ apply suggested refactor inline
              │ re-emit edit
              ▼
PostToolUse  ─┐
              │ re-check violation count on written file
              │ verify ratchet: count_after <= count_before
              │ if violated: rollback edit, emit diagnostic
              ▼
Commit-msg   ─┐
              │ Broken Windows ratchet: total count per changed file
              │ reject commit if any file's violation count grew
              ▼
```

### What "suggested auto-refactor diff" looks like

PreToolUse emits structured output the model can consume:

```json
{
  "principle": "long_parameter_list",
  "severity": "BLOCK",
  "location": "lib/foo.py:42",
  "violation": "def process(a, b, c, d, e, f) — 6 positional args, max 4",
  "suggested_refactor": "from dataclasses import dataclass\n\n@dataclass\nclass ProcessArgs:\n    a: int\n    b: str\n    c: float\n    d: bool\n    e: Path\n    f: list\n\ndef process(args: ProcessArgs) -> ...",
  "ratchet_delta": "+1 long_parameter_list violation (0 → 1)"
}
```

Model sees this, rewrites the edit inline per Rule 18, re-emits. No human loop needed for deterministic cases.

## Integration with existing infrastructure

| Extends | Purpose |
|---------|---------|
| `lib/complexity_profiler.py` | CC + Cognitive Complexity computation |
| `hooks/quality-pretool-hook.py` (existing PreToolUse) | Extend from DRY/KISS to full classic 8 + new additions |
| `rules/cc-enforcement.md` | Trust-level thresholds (STRICT / DEFAULT / RELAXED) apply to this matrix |
| `lib/hypothesis_engine.py` | Register a falsifiable claim per principle (e.g. "LoD detector reduces coupling-complexity CV by ≥20% over 30 days") |
| `logs/quality-audit.jsonl` | AUDIT-LOG tier sink |
| `logs/broken-windows.jsonl` | Ratchet activity sink |
| `lib/gates/principles_criterion.py` | Existing principles-criterion skill — extend to emit ratchet deltas |

## Implementation priority order

If forced to pick sequence (and the preplan picks it):

1. **Broken Windows ratchet** (meta-gate; makes everything else meaningful)
2. **Law of Demeter detector** (single biggest coupling win, trivially mechanical)
3. **Cognitive Complexity detector** (CC's more sophisticated cousin)
4. **Long Parameter List + Dead Code** (cheap wins, bundle in one PR)
5. **Classic 8 completion** (promote SRP/ISP to BLOCK tier; GRASP to WARN tier)
6. **Test-Existence Floor** (paired with Rule 14 Goodhart Guard)
7. **Magic Numbers / Nested Conditionals** (lowest priority — nice-to-have polish)

## Hypothesis registration (paste-ready)

```bash
PYTHONPATH=$HOME/.claude python3 $HOME/.claude/lib/hypothesis_engine.py register \
  --pr 0 \
  --claim "The expanded gate matrix (LoD + CogC + Broken Windows ratchet + SRP/ISP BLOCK) reduces per-file violation count by >=30% over 60 days on actively-edited files, with zero net new violations on any ratcheted file." \
  --metric per_file_violation_delta \
  --prediction "median file shows 30%+ violation count reduction; max file shows 0% increase" \
  --deadline "2026-06-17"
```

## Falsification

The matrix is wrong if:

- BLOCK tier creates friction that reduces commits-per-token ratio by >15% (Goodhart: developers stop committing rather than stop violating)
- Auto-refactor diffs produce regressions (tests fail after suggested refactor is applied) at >5% rate
- Broken Windows ratchet blocks legitimate refactors that intentionally trade local violations for global improvement (e.g. introducing a factory that increases LoD count locally but removes duplication across 20 files)

If any of those fire, tiers get downgraded. WARN is safer than BLOCK when false positives exist.

---

V>>--<<V · docs/gate-quality-matrix.md · gen 482 · authored for the /fibo-rsi ultrado execution
