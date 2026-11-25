# The Principled Codebase: Design Principles as Convergence Signals

*Every team has the principles poster on the wall. Almost none of them can tell you whether last week's refactor moved the codebase closer to or further from those principles.*

---

## The Checklist Problem

Design principles as checklists fail for the same reason any metric fails once it becomes a target: Goodhart's Law. The moment SOLID compliance becomes a thing you report on, engineers write code that satisfies the checklist rather than the intent behind it. Five-line interfaces that technically have single responsibilities but share a god-object coordinator. Dependency injection containers that invert dependencies on paper while tightly coupling everything to the container itself.

The problem is not the principles. The problem is treating them as binary gates — compliant or non-compliant — instead of signals on a spectrum.

Better framing: use design principles as dimensions in a convergence criterion. Not "does this code follow KISS?" but "by how much did this change reduce cognitive complexity?" Not a checklist. A measurable direction.

---

## Principles as Criterion Dimensions

GRIP's `converge()` loop works by repeatedly applying an improvement function `f` to a codebase state until the state stabilises — or more precisely, until it approaches a fixed point. The convergence criterion is a composite score across six principle dimensions. Each dimension maps to a computable metric:

| Principle | Signal | Metric |
|-----------|--------|--------|
| DRY | Duplication density | `dup_density`: duplicated line ratio across the module |
| KISS | Cognitive complexity | `avg_complexity`: average cyclomatic complexity per function |
| YAGNI | Dead abstraction count | `unused_abstractions`: classes/functions with zero call sites |
| SOLID | Coupling ratio | `coupling_ratio`: average external dependencies per class |
| GRASP | Responsibility spread | `responsibility_skew`: standard deviation of method count per class |
| YSH | Obvious gap count | `ysh_gaps`: patterns recurring 3+ times without abstraction |

These are not perfect metrics. `avg_complexity` does not distinguish a necessary 12-branch state machine from an unnecessary 12-branch if-else chain. But they do not need to be perfect — they need to be directional. The question is not "is this score high enough?" but "is this score improving?"

---

## Scoring a Refactor Through Three Depths

Here is a concrete example: a 400-line authentication module with mixed concerns. The initial state:

```
Depth 0 (baseline)
  dup_density:          0.31   (31% of lines are near-duplicates)
  avg_complexity:       8.4    (high — several nested conditionals)
  unused_abstractions:  3      (TokenValidator, BaseAuth, ISession)
  coupling_ratio:       5.1    (each class imports ~5 others)
  responsibility_skew:  14.2   (AuthManager has 23 methods, others have 2-4)
  ysh_gaps:             2      (retry logic appears 4x, no abstraction)

  composite: 0.38   (lower is worse — 0 is fully principled, 1 is chaos)
```

After depth 1: extract the retry logic into a `RetryPolicy` class, delete `BaseAuth` (zero call sites), flatten one nested conditional:

```
Depth 1
  dup_density:          0.21   (-10 points — retry deduplication)
  avg_complexity:       6.1    (-2.3 — flattened conditional)
  unused_abstractions:  2      (-1 — BaseAuth deleted)
  coupling_ratio:       4.8    (-0.3 — minor improvement)
  responsibility_skew:  11.4   (-2.8 — one method moved to RetryPolicy)
  ysh_gaps:             0      (-2 — retry pattern now abstracted)

  composite: 0.51   contraction ratio L = 0.49
```

After depth 2: split `AuthManager` by the GRASP Information Expert pattern — session handling to `SessionManager`, token operations to `TokenStore`:

```
Depth 2
  dup_density:          0.19   (-2 points)
  avg_complexity:       5.3    (-0.8)
  unused_abstractions:  2      (stable)
  coupling_ratio:       3.9    (-0.9 — smaller classes, fewer imports needed)
  responsibility_skew:  4.1    (-7.3 — the big move)
  ysh_gaps:             0      (stable)

  composite: 0.67   contraction ratio L = 0.56
```

Two things are visible in this trace. First, the contraction ratio L remains below 1 at every depth — the loop is genuinely converging. Second, no single depth is complete: depth 1 mostly attacks DRY and YAGNI; depth 2 mostly attacks GRASP and SOLID. The composite score ensures you do not declare victory after fixing only the dimension you noticed first.

---

## Why This Resists Goodhart

The Goodhart problem with checklists is that any single metric can be gamed without improving underlying quality. A high-dimensional composite is structurally more resistant: improving one dimension at the expense of others is visible in the score.

Extract functions purely to reduce `avg_complexity` — breaking natural units into fragments — and `dup_density` rises, `responsibility_skew` may worsen. Delete abstractions to reduce `unused_abstractions` that are actually planned extension points, and `coupling_ratio` rises when the next feature ships. Each principle constrains the others.

This is not a guarantee of correctness. A sufficiently motivated engineer can still game a multi-dimensional score. But the effort required to game six correlated metrics simultaneously is substantially higher than gaming a checklist. In practice, when a dimension worsens, the author has to justify it explicitly — more useful than a green checkbox.

---

## The Sixth Principle: YSH

YSH (You Should Have) is the dialectical inverse of YAGNI. YAGNI: don't build what you don't need yet. YSH: don't fail to build what any competent engineer would have built.

In convergence terms, YSH is a gap detector. The `ysh_gaps` metric counts patterns that have appeared three or more times without abstraction. The threshold is three: first occurrence, just write it; second occurrence, note it; third occurrence, the pattern is structural not coincidental.

When `ysh_gaps > 0`, the convergence loop has found a YAGNI/YSH misalignment — code correctly deferred at occurrences 1 and 2 but overdue for abstraction at occurrence 3. The metric does not prescribe the solution; it marks the decision point. Checklists collapse this tension into a binary. The composite score surfaces it at exactly the right moment.

---

## The Composite Score

The composite is a calibrated weighted average:

```
composite = 1 - (
  0.25 * normalize(dup_density) +
  0.20 * normalize(avg_complexity) +
  0.15 * normalize(unused_abstractions) +
  0.20 * normalize(coupling_ratio) +
  0.10 * normalize(responsibility_skew) +
  0.10 * normalize(ysh_gaps)
)
```

Weights reflect practical impact. DRY carries the highest because duplicated business rules compound — every copy is a future divergence bug. GRASP responsibility skew is the leading indicator I watch most in early-stage codebases: when one class has 20 methods and three others have 2, the design is already failing.

Normalisation is critical. Raw `avg_complexity` is not comparable to raw `dup_density`. Each metric is normalised against a baseline for the codebase type (web API, CLI, data pipeline) — otherwise a parser scores poorly on KISS regardless of whether the complexity is justified. An honest limitation: the baselines require domain knowledge. The criterion measures direction, not absolute quality.

---

## What This Is Not

Not a linter configuration. Linters catch violations at a point in time. The convergence criterion measures trajectory across iterations — a codebase can have zero linter violations and a worsening composite score if each "fix" pays off one metric while degrading another.

Not a replacement for code review. The metrics flag where to look, not what to do. `responsibility_skew` told us a split was warranted on `AuthManager` — applying GRASP's Information Expert pattern still required reading the code.

What it replaces is the end-of-sprint question "did we make the code better?" with something answerable: composite moved from 0.38 to 0.67 across two depths, contraction ratio below 1, largest remaining gap is `coupling_ratio`. The principles poster cannot give you that.

---

*Primary Author: LC Scheepers*
