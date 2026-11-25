---
name: optimizing-complexity
description: Enforces algorithmic complexity awareness as a design principle. Flags O(n^2)+ patterns in code and requires documented justification. Use when writing, reviewing, or refactoring algorithms.
effort: low
status: Active
version: 1.0.0
triggers:
  - algorithm design
  - performance review
  - nested loops
  - complexity analysis
  - "Big O"
  - quadratic
  - exponential
integrates:
  - design-principles
  - principles-criterion
see_also:
  - code-mode
  - architect-mode
  - review-mode
---

# Optimizing Complexity (BIG-O)

Prefer the lowest complexity class that solves the problem. Elegance is O(n log n) where others reach for O(n^2).

## The Rule

| Complexity | Verdict | Action |
|------------|---------|--------|
| O(1), O(log n) | Optimal | Ship it |
| O(n) | Good | Ship it |
| O(n log n) | Acceptable ceiling | Ship it |
| O(n^2) | Requires justification | Document why no better algorithm exists |
| O(n^3)+ | Reject by default | Refactor or prove the problem demands it |
| O(2^n), O(n!) | Hard problems only | Must cite NP-hardness or equivalent proof |

The ceiling is **O(n log n)** for general-purpose code. Above that is technical debt.

## Detection Patterns

### Nested Loops (O(n^2) signal)

```python
# BAD: O(n^2) — linear scan inside a loop
for user in users:
    for order in orders:
        if order.user_id == user.id:
            process(user, order)

# GOOD: O(n) — index first, then single pass
orders_by_user = defaultdict(list)
for order in orders:
    orders_by_user[order.user_id].append(order)
for user in users:
    for order in orders_by_user.get(user.id, []):
        process(user, order)
```

### Repeated Lookups (O(n) per lookup signal)

```python
# BAD: O(n) lookup in list, O(n*m) total
if item in large_list:  # list.__contains__ is O(n)

# GOOD: O(1) lookup in set
if item in large_set:   # set.__contains__ is O(1) avg
```

### Recursive Without Memoization (O(2^n) signal)

```python
# BAD: O(2^n) — exponential branching
def fib(n):
    if n <= 1: return n
    return fib(n-1) + fib(n-2)

# GOOD: O(n) — memoized
from functools import lru_cache

@lru_cache(maxsize=None)
def fib(n):
    if n <= 1: return n
    return fib(n-1) + fib(n-2)
```

## Common Upgrades

| From | To | How |
|------|----|-----|
| O(n^2) nested search | O(n) hash join | Build dict/set, then single pass |
| O(n) repeated lookup | O(1) amortized | Use set or dict instead of list |
| O(n^2) sort (bubble/insertion) | O(n log n) | Use built-in sort (Timsort) |
| O(2^n) recursion | O(n) or O(n log n) | Memoize or convert to DP |
| O(n) string concat in loop | O(n) total | Use `''.join(parts)` |

## Decision Gate

```text
Writing a loop or algorithm?
  +-- How many elements? n < 100 always? --> O(n^2) tolerable, note it
  +-- n unbounded or large?
       +-- Can you use a hash map? --> O(n) single pass
       +-- Need ordering? --> O(n log n) sort then scan
       +-- Neither works? --> Document why O(n^2)+ is necessary
```

**Gate Q warning**: Gate Q uses a fast indentation heuristic instead of AST parsing
because it runs during every Write/Edit (performance-critical). It may warn on
sequential loops at different indent levels (e.g., `for...else` patterns). A warning
means "review this" not "this is definitely quadratic." For offline codebase audits,
use `lib/complexity_analyzer.max_complexity_class()` for precise O(n^2) detection.

## Metric: max_complexity_class

Computable signal for the Principled Codebase convergence loop.
Implementation: `lib/complexity_analyzer.py` (tested, 20 tests).

```python
from lib.complexity_analyzer import max_complexity_class
import ast

tree = ast.parse(open("module.py").read())
depth = max_complexity_class(tree)  # 0=O(1), 1=O(n), 2=O(n^2)
assert depth <= 1, f"BIG-O violation: nesting depth {depth}"
```

Threshold: `max_complexity_class <= 1`. No function should have nesting depth > 1 without documented justification.
Lambdas and nested functions are treated as separate scopes.

## Structural Validation (Gentner Enforcer)

When complexity claims involve structural analogies (e.g., "this pattern is like merge sort",
"this architecture mirrors divide-and-conquer"), validate with the Gentner enforcer agent:

```python
Task(
    prompt="Validate the structural mapping: '[source] maps to [target]'. "
           "Check relational correspondence, not surface similarity.",
    subagent_type="gentner-enforcer"
)
```

The Gentner enforcer catches surface-level analogies that mask complexity issues:

| Claim | Gentner Verdict | Why |
|-------|----------------|-----|
| "Our search is like binary search" | FAIL if linear scan | Surface: both "search". Structure: no halving |
| "Pipeline pattern like Unix pipes" | PASS if streaming | Relational: data flows one-way, each stage transforms |
| "Observer pattern like pub/sub" | PASS if decoupled | Relational: one-to-many notification, loose coupling |
| "Our cache is like a hash map" | FAIL if O(n) eviction | Surface: both "store data". Structure: eviction changes complexity |

Use when reviewing architecture docs, design pattern claims, or complexity justifications
that rely on analogy rather than proof.

## Anti-Patterns

| Anti-Pattern | Complexity | Fix |
|--------------|-----------|-----|
| Nested `for` with membership test | O(n*m) | Build set, single pass |
| Sort inside loop | O(n^2 log n) | Sort once outside loop |
| String `+=` in loop | O(n^2) amortized | `''.join()` |
| `list.index()` in loop | O(n^2) | Dict lookup |
| Recursive tree without memo | O(2^n) | `@lru_cache` or DP table |

## Examples

| Scenario | Principle | Action |
|----------|-----------|--------|
| "Find duplicates in list" | BIG-O | Use set, not nested loop: O(n) vs O(n^2) |
| "Sort then binary search" | BIG-O | O(n log n) + O(log n) = O(n log n) total, acceptable |
| "Check all pairs" | BIG-O | O(n^2) — justify or find O(n log n) alternative |
| "Brute force TSP" | BIG-O | O(n!) — cite NP-hardness, use approximation |
