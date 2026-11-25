---
name: principles-criterion
description: Measure DRY/KISS/CC/BIG-O/SOLID/GRASP/YAGNI/YSH as convergence signals. Maps each design principle to a computable metric. Powers the Principled Codebase convergence loop.
status: Active
effort: medium
version: 1.0.0
triggers:
  - /principles
  - /design-check
  - /principle-scan
integrates:
  - converge
  - attest
  - design-principles
  - cube-session-wire
lineage: genesis:github-issue-205
---

# principles-criterion

Measure all 8 GRIP design principles as computable, automatable convergence signals. Each principle maps to a metric derived from AST analysis, import graphs, or external scanners.

## 8-Principle Mapping

| Principle | Metric | Scanner | Score Formula |
|-----------|--------|---------|---------------|
| DRY | `dup_density` | `dedup_scanner.py` | `1 - dup_density` |
| KISS | `avg_complexity` | AST cyclomatic | `1 - (avg_cc - 1) / 19` |
| CC | `cc_offender_count` | AST cyclomatic | `1 - (offenders / total) * 5` |
| BIG-O | `max_loop_depth` | AST loop nesting | `1 - (quadratic / total) * 5` |
| SOLID | `coupling_ratio` | `dependency_mapper.py` instability | `1 - max(0, I - 0.3) / 0.7` |
| GRASP | `responsibility` | AST methods/class | `1 - max(0, avg_methods - 5) / 10` |
| YAGNI | `dead_code_ratio` | `yagni_scanner.py` | `1 - dead_ratio` |
| YSH | `pattern_count` | AST function names | `1 - (patterns / files) / 2` |

Composite score: weighted average with DRY/SOLID/YAGNI/YSH at 0.15, KISS/CC/BIG-O/GRASP at 0.10.

## CLI Usage

```bash
# Scan a codebase
python3 lib/principles_criterion.py [path]

# Scan with convergence loop
python3 lib/principles_criterion.py [path] --converge --target 0.80

# Verbose output
python3 lib/principles_criterion.py lib/ --verbose
```

## Python API

### `principles_scan(path: str) -> PrinciplesReport`

Scan a directory and return scores for all 8 principles.

```python
from lib.principles_criterion import principles_scan

report = principles_scan("lib/")
print(report.summary())
print(f"Composite: {report.composite_score:.1%}")
```

### `principles_as_attest_signals(report) -> list[Signal]`

Convert a report into `Signal` objects for `AttestCriterion` integration.
Returns 8 signals (one per principle) with threshold-based checks.

```python
from lib.principles_criterion import principles_scan, principles_as_attest_signals

report = principles_scan(".")
signals = principles_as_attest_signals(report)
for s in signals:
    print(f"{s.name}: {'PASS' if s.check(None) else 'FAIL'}")
```

### `converge_principles(path, max_depth, target) -> ConvergenceResult`

One-line convergence entry point. Scans, evaluates criterion, re-scans on failure.

```python
from lib.principles_criterion import converge_principles

result = converge_principles("lib/", max_depth=5, target=0.80)
print(result.best_state.summary())
print(f"Converged: {result.satisfied} in {result.iterations} iterations")
```

### `wave_aware_max_depth(default: int) -> int`

Reads the current Fibonacci wave position and maps to convergence depth.
Falls back to `default` when no active wave session exists.

| Wave Position | max_depth |
|--------------|-----------|
| 1 | 2 |
| 2 | 3 |
| 3 | 4 |
| 5 | 5 (capped at 6) |
| 8 | 6 (capped at 6) |

## Score Interpretation

| Grade | Range | Meaning |
|-------|-------|---------|
| A+ | 90-100% | Exemplary adherence |
| A | 80-89% | Production quality |
| B | 70-79% | Acceptable, minor improvements needed |
| C | 60-69% | Attention needed |
| D | 50-59% | Significant refactoring required |
| F | <50% | Critical violations |

## Convergence Integration

The skill integrates with GRIP's convergence framework as criterion, scorer, and improve:

```python
from lib.converge import converge_iter
from lib.principles_criterion import (
    principles_scan,
    principles_criterion,
    principles_scorer,
    principles_improve,
)

result = converge_iter(
    state=principles_scan(path),
    criterion=principles_criterion(target=0.80),
    improve=principles_improve,
    max_depth=5,
    _scorer=principles_scorer,
    label="principles",
)
```

## CUBE Integration

Signals are wired into the 4D CUBE via `cube_session_wire.py`:

```python
from lib.cube_session_wire import wire_principles
result = wire_principles(".", session_id="abc123")
```

Each signal maps to a CUBE domain with convergence depth based on score:
- Score >= 0.70: CODIFIED
- Score >= 0.50: UNDERSTOOD
- Score < 0.50: DETECTED

## Related

- `lib/principles_criterion.py` — Core scanner and convergence logic
- `lib/dependency_mapper.py` — Fan-in/fan-out and instability metrics for SOLID
- `lib/wave_state.py` — Fibonacci wave positions for convergence pacing
- `lib/dedup_scanner.py` — DRY duplication detection
- `lib/yagni_scanner.py` — Dead code detection for YAGNI
- `skills/design-principles/SKILL.md` — Principle definitions and rationale
