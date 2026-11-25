---
name: sprint
description: Wrapper around /rsi-optimal that defers per-wave shipping and produces a single delivery PR at sprint end. Composition only — calls rsi-optimal with --no-ship, then runs /auto-ship once for the batched delivery. Use when you want N hours of work to land as ONE PR instead of N per-wave PRs.
status: Active
effort: low
version: 1.0.0
triggers:
  - /sprint
  - run sprint
  - full sprint with batch delivery
  - sprint with one pr
integrates:
  - rsi-optimal/SKILL.md
  - auto-ship/SKILL.md
see_also:
  - rsi-optimal
  - auto-ship
  - bg-dispatch
  - ultrado
---

# /sprint — Single-Delivery RSI Sprint Wrapper

Composition skill. Runs the unified `/rsi-optimal` sprint with per-wave
shipping deferred, then runs `/auto-ship` once at the end. Result: an
N-hour sprint produces a single delivery PR instead of one PR per wave.

**Plain English summary**: same as `/rsi-optimal` (autonomous improvement
cycle with Fibonacci waves and hypothesis registration), but instead of
shipping after each wave, it accumulates all changes on the worktree
branch and ships them as one PR at the end. Useful when you want a single
reviewable delivery rather than N per-wave PRs.

## When to Use This vs `/rsi-optimal` vs `/ultrado`

| Skill | Per-wave ship? | When |
|-------|----------------|------|
| `/rsi-optimal` | YES (default) | Want incremental delivery; reviewers prefer small PRs |
| `/sprint` | NO (deferred to end) | Want one reviewable delivery at sprint end |
| `/ultrado` | per-wave | MYTHIC tier — recursive falsification, never-stop semantics |

`/sprint` and `/rsi-optimal --no-ship` are functionally equivalent. The
wrapper exists because the `--no-ship + final ship` pattern proved 3+
times across V>>'s 2026-04 sessions, satisfying YSH (You Should Have)
abstraction.

## Usage

```bash
/sprint                               # Default 3h, infer goal, single PR at end
/sprint "refactor ISP decomposition"  # 3h focused sprint, one PR
/sprint 5h "rewrite auth"             # 5h sprint, one PR
/sprint 8h --depth 11 "formal proofs" # Long sprint with overridden depth
```

## Composition Contract

`/sprint` invokes:

1. `/rsi-optimal {duration} {goal} --no-ship {other-flags}` — runs the
   full sprint with per-wave shipping disabled. All wave changes
   accumulate on the worktree branch.
2. After all waves complete, `/auto-ship` runs once on the worktree —
   scans accumulated changes, groups them into commits if not already
   committed (rsi-optimal commits during wave execution), pushes the
   branch, opens a single PR, and merges it.

The wrapper does NOT:
- Override any rsi-optimal phase logic
- Bypass yolo, hypothesis registration, worktree isolation, or external
  council scoring (all inherited)
- Force-push (Rule 4 of bg-dispatch — never bypassed by yolo)

## Parameters

`/sprint` passes all parameters through to `/rsi-optimal` except
`--no-ship`, which it forces to `true` (the wrapper's whole purpose).

| Parameter | Default | Description |
|-----------|---------|-------------|
| duration | 3h | Sprint duration (1h–8h) |
| goal | inferred | What to work on |
| --depth N | auto | Convergence depth override |
| --no-fibonacci | false | Flat execution, no wave scheduling |
| --no-delegate | false | Disable Opus/Sonnet split |
| --no-hypothesis | false | Skip formal hypothesis registration (NOT recommended) |

## Execution Sequence

When invoked, execute without stopping for confirmation:

1. Compute `duration` from arguments (default 3h, max 8h).
2. **RTK baseline snapshot** — record token-savings baseline before any work:
   ```bash
   python3 $HOME/.claude/lib/rtk_sprint_dividend.py snapshot
   ```
   Outputs one line confirming baseline captured. Uses active RSI session ID automatically.
3. Invoke `/rsi-optimal {duration} {goal} --no-ship` — yields control to
   rsi-optimal which handles worktree, yolo, planning, waves, breathing.
4. At each **wave boundary** (after each Fibonacci wave completes), record delta:
   ```bash
   python3 $HOME/.claude/lib/rtk_sprint_dividend.py wave N
   ```
   Outputs one line showing tokens saved that wave + compression bar.
5. When rsi-optimal returns (sprint duration elapsed or all waves done):
   ```bash
   cd "${WT}"  # rsi-optimal exposes the worktree path; reuse it
   /auto-ship   # 6-stage delivery on the accumulated changes
   ```
6. **RTK Sprint Dividend** — render the full savings report:
   ```bash
   python3 $HOME/.claude/lib/rtk_sprint_dividend.py report
   ```
   Shows sprint savings, session total, projected monthly offset, and any
   lifetime milestone unlocked.
7. Output the resulting PR URL.
8. Anti-drift Rule 11 tight 4-line template (Done implicit in PR title).

## Wave Dispatch Pattern (Shape A+C mixing)

When a wave has 2+ independent tasks, classify each task before dispatching:

- **Cost-bearing tasks** — multi-step diagnosis, multiple files to read,
  signature verification, mock infrastructure design, anything where parallel
  reading buys time. → Dispatch a subagent (Shape A element).
- **Trivial-and-known tasks** — specific decorator additions, one-line fixes
  with known root cause, regex-replace-style edits, scope fully known from
  prior context. → Execute directly in main session (Shape C element).

**Mix Shape A + Shape C in one wave** when their tasks touch different files
(no shared mutable substrate). The trivial inline edits run in parallel with
the dispatched agents, saving agent-spawn overhead on tasks that wouldn't
benefit from parallel reading. The wave's PR contract is unchanged: all
changes accumulate, single delivery PR at sprint end.

**Empirical evidence:**
- /sprint W1 (PR #2385, 2026-04-27) — 3 cost-bearing tasks, pure Shape A
  (3 agents). ~25 min wall-clock.
- /sprint W2 (PR #2387, 2026-04-28) — 2 cost-bearing tasks (diagnosed via
  agents) + 1 trivial task (xfail decorators, ~10 lines, known scope)
  executed direct. Saved 1 agent-spawn overhead. ~28 min wall-clock with
  greater scope range (trivial + small + medium).

**Anti-pattern**: dispatching a subagent for a task whose scope is fully
known and whose edit is trivial (~10 lines, no investigation). Pays the
agent-spawn overhead with no parallel-reading benefit.

**Anti-pattern**: mixing Shape A + Shape B in one wave — parallel writes
from the Shape A agents will race the Shape B coordinator's substrate
sequence (per `skills/bg-dispatch/SKILL.md` Rule 2).

## Why a Wrapper, Not a Flag

`/rsi-optimal --no-ship` exists and works. `/sprint` is a separate skill
because:

1. **Discoverability**: operators searching for "single PR sprint" find
   `/sprint` directly via the `triggers` list, no need to know the flag.
2. **Composition pattern proof**: `/sprint` is the canonical example of
   "wrap an existing skill with a forced flag + post-hook". Future
   composition skills can mirror its structure.
3. **Final-ship guarantee**: a flag is easy to forget; a wrapper enforces
   the post-step. If the operator passes `--no-ship` to `/rsi-optimal`
   directly and forgets the final `/auto-ship`, changes sit on a
   worktree branch un-PR'd. `/sprint` cannot leave that state.

## Anti-Patterns

- Do NOT call `/sprint --no-ship` (the flag is force-forced; the inverse
  is invoking `/rsi-optimal` directly).
- Do NOT skip the final `/auto-ship` step — the wrapper's whole value is
  the deferred batched delivery.
- Do NOT run `/sprint` if you want incremental per-wave PRs — use
  `/rsi-optimal` directly instead.
- Do NOT mix `/sprint` with `/ultrado` in the same session — they have
  conflicting yolo/worktree state machines.

## Falsifier

This skill is wrong if, over four weeks of activated usage:
- It produces more than one PR per invocation (composition contract
  violated), OR
- The final `/auto-ship` step is consistently bypassed (wrapper failed
  its core promise), OR
- Operators report they wanted per-wave PRs and forgot the wrapper
  defers them (discoverability backfire).

Track via:
- PR count per `/sprint` invocation (must equal 1 unless rsi-optimal
  fails mid-sprint and partial state is preserved).
- `learning/insights.jsonl` for operator feedback tags.

## Tests

`tests/test_rsi_sprint_merge.py` covers the composition contract:
- `/sprint` triggers list contains `/sprint` and `run sprint`
- skill frontmatter integrates rsi-optimal + auto-ship
- composition is documented (rsi-optimal --no-ship → final /auto-ship)

Goodhart-resistant: tests assert returned values from a parsed YAML
frontmatter, not call counts on a mock.

## Lessons Learned

---

### [Design Pattern] 2026-04-29 15:49 UTC

- The /sprint cascade pattern is generalisable beyond embeddings. Any sprint that touches K legacy files in a codebase pre-dating the gate thresholds will hit the same Gate V cascade structure. The empirical bound is "files clean enough to migrate without a refactor pass" — this is a property of the code, not the migration. Future sprints with high-fan-out mechanical changes should pre-survey caller files for KISS/BIG-O violations before estimating scope.
- The HAL shim (`lib/hal/embeddings.py`) is a clean reference implementation of the syscall doctrine for a *new* boundary. Compare to `lib/slack_transport.py` (PR #2380) which extracted from existing scattered call sites — different starting state, same end state. Both shims share: stable public API, mockable seam, audit log shape, env-var-driven dispatch, plain-language errors. This convergence is evidence the doctrine is producing a coherent architectural language across boundaries.
- H217's PARTIAL outcome is more valuable than a forced CONFIRMED would have been. A migrated 16/16 with bundled refactors would have hidden the cascade pattern; the 8/16 outcome surfaces it as a structural finding now codified in `feedback_embeddings_migration_cascade.md`. Per `rules/scientific-method.md`'s ">70% confirmation = suspiciously easy" — partial honesty is the right epistemic posture.



---

### [Design Pattern] 2026-04-29 15:36 UTC

- The cascade pattern is structural, not incidental. GRIP's gate thresholds (KISS=50 lines, BIG-O=any nested for) flag every legacy file pre-existing complexity. Migration touches a file → PostToolUse re-evaluates → finds pre-existing violations → cache locks editing to that file. Each file becomes a refactor PR-by-stealth. KISS enforcement, ironically, prevents KISS migrations.
- The right response is the one the plan already prescribed for `learning_detector.py`: defer files with pre-existing violations to focused-refactor PRs. The empirical bound on /sprint scope is "files clean enough to migrate without a refactor pass" — not "all callers". This is the same shape as Rule 19 ("no issue hierarchy") but inverted — sometimes the right move IS to defer, with the understanding that the deferral creates a tracked follow-up, not silent debt.
- The hypothesis ">=10 callers" was set without seeing the cascade pattern. Per `rules/scientific-method.md` H217 should be marked INCONCLUSIVE with the empirical reason — that's the system *learning*, not failing.

