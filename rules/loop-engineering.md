# Loop Engineering — the One Kernel Every GRIP Loop Already Is (PARAMOUNT)

Origin: 2026-06-24, wf_9dd7d69a. While building the decision-chain publisher
(itself a loop), an inventory found that GRIP's recursive systems are NOT five
different designs — they are **five instantiations of ONE function signature**
already shipped and tested. This rule is a **NAME for that proven structure**, not
new machinery (per `knowledge-maturation-functor.md`: the pattern is N=5 emergent
in-tree, so it earns a rule — and the kernel `lib/converge.py` already IS the
mechanical floor, so no new abstraction is built). The anti-theatre gate
(`rules/loop-priority-external-over-theatre.md`, `skills/create`) is satisfied
because this crystallises *existing* code into a reusable concept; it ships zero
speculative loop machinery.

## The kernel (one signature, verified across five loops)

> Every GRIP recursive system is `converge(state, criterion, improve, max_depth)`:
> apply `improve(state)` until `criterion(state)` is True or depth hits the
> ceiling; return the best state seen. The canonical implementation is
> `lib/converge.py:66` (`def converge(...)` → `ConvergenceResult`).

| # | Loop | state | criterion | improve | Verified anchor |
|---|------|-------|-----------|---------|-----------------|
| 1 | `/converge` (code) | any value | caller `callable(s)→bool` | caller `callable(s)→s` | `lib/converge.py:66` |
| 2 | `/evo` (facet evolution) | `EvoState` | `s.confidence >= 0.8` | `evo_improve(s)` | `lib/evo.py:567,604`; `EvoState` `:224` |
| 3 | Fibonacci-wave RSI (`/auto`, `/sprint`) | wave state + lessons | wave executor's `criterion_met` | `run_waves()` threads `FIBONACCI[n]` depth | `lib/rsi/optimal.py:45`; `lib/converge_loop.py:105` |
| 4 | Learning proposer | captured-error rows | idempotence (pure projection) | `propose(rows)` | `lib/learning_proposer.py:264`; selftest `:438` |
| 5 | Auto-converge RSI | `RSIState` | `rsi_criterion(s)` (test≥95% ∧ review≥85 ∧ meta≤25%) | next-candidate exec | `lib/auto_converge.py:62,96`; imports `converge` `:46` |
| + | Decision-chain publisher | IDR chain rows | publishable ∧ Banach fixed point | `render_site(rows)` | `lib/decision_chain_publish.py` (this wf) |

Loop 4 and the publisher are the **degenerate (zero-improve) case**: a pure
deterministic projection whose criterion is *idempotence itself*
(`publish(publish(R)) == publish(R)`). Both ship a loud-failing `selftest_idempotent`
proving the Banach fixed point (`lib/learning_proposer.py:438`,
`lib/decision_chain_publish.py`). The kernel still applies — `improve` is the
identity and `criterion` is "already a fixed point".

## Loop Engineering — the meta-capability (the /ultrawin)

> **Loop Engineering** is GRIP autonomously *recognising* that a goal needs a loop,
> *instantiating* it on the one kernel signature, *running* it to a falsifiable
> criterion, *measuring* it, and *improving* it — across harnesses — without a
> human rewriting the loop each time.

It is a recognition discipline first, code second: most "new loops" are NOT new
code — they are the existing kernel applied to a new `(state, criterion, improve)`
triple. The engineering is choosing those three honestly:

- **state** — what is being improved (serialisable; flows with prior lessons).
- **criterion** — the stop condition, and it MUST be falsifiable AND, wherever the
  loop's output is the model's own work, **exogenous** (`exogenous-anchor-law.md`:
  CI on mutation-surviving tests, a cross-provider verdict, a human merge, a
  deadline-verified hypothesis — never the loop's own self-score). A self-evaluated
  criterion is hallucinated convergence, not convergence.
- **improve** — one step, pure/idempotent where possible (so re-running is safe and
  the fixed point is provable).

## The five binding constraints (when engineering ANY loop)

1. **Reuse the kernel.** A new loop instantiates `lib/converge.py` (or the
   wave/accumulate variants) — it does not hand-roll its own iterate-until-done.
   New iterate-until-done code is the smell this rule exists to stop.
2. **Falsifiable + exogenous criterion.** State what makes the loop WRONG, and
   anchor "done" to something the loop cannot please (`exogenous-anchor-law.md`,
   `scientific-method.md`). No criterion → not a loop, a `while True`.
3. **Idempotent where the loop is a projection.** A render/publish/propose loop
   ships a `selftest_idempotent` that FAILS LOUDLY on drift (the Banach proof). The
   degenerate loops (4, publisher) are pure functions; prove it.
4. **External value over internal theatre.** Per
   `loop-priority-external-over-theatre.md`: a loop that only makes GRIP "feel more
   capable" is theatre-until-proven. Engineering a loop must trace to an outside
   signal (a user, a payer, a recurring external failure removed) — building loop
   machinery for its own sake is the exact misallocation that rule forbids.
5. **No stubs in the loop body.** Per `no-stubs.md`: `improve` and `criterion` are
   fully implemented, never a hollow placeholder that names a step it does not
   perform.

## Why this is a rule and not a new `lib/loop_engineering.py`

The kernel ALREADY exists and is ALREADY reused five times — abstracting a *new*
"loop instantiator" on top would be premature (YAGNI / YSH-inverse) and would add a
shim nobody asked for. The five loops do not share a missing helper; they share a
*concept* that was unnamed. A rule names the concept (push-based, every session);
the kernel stays the mechanical floor. If two loops are ever found to copy-paste
the same *wiring* around the kernel (detection → instantiate → measure), THEN
extract the wiring per `knowledge-maturation-functor.md` (rule now → shim at N=2 of
the *wiring*, not the kernel).

## Falsification

Wrong if, over the next ~10 loops GRIP builds: (a) a genuinely new loop cannot be
expressed as `converge(state, criterion, improve, max_depth)` without distortion
(the unifying claim is false — the kernel is not universal, narrow it); OR (b)
naming the concept changes no behaviour (loops are still hand-rolled, criteria
still self-scored) — then the rule is inert decoration, delete it; OR (c) the
"reuse the kernel" constraint forces a worse design on a loop that genuinely needed
bespoke control flow (over-applied — relax constraint 1 to advisory). Track: new
loops expressed on the kernel vs hand-rolled (target: kernel ≥80%); loops shipping
a falsifiable+exogenous criterion (target 100%); idempotent projections shipping a
loud selftest (target 100%).

## Scope

Every recursive/iterate-until-done system in GRIP and HAL: `/converge`, `/evo`,
`/auto`, `/sprint`, `/rsi`, the learning proposer, auto-converge, the
decision-chain publisher, and any future loop. Sits beside `exogenous-anchor-law.md`
(loop validity — the criterion must be exogenous), `loop-priority-external-over-theatre.md`
(loop priority — spend loops on external value), `scientific-method.md` (falsifiable
criteria), `no-stubs.md` (no hollow loop bodies), `knowledge-maturation-functor.md`
(this rule's own N=5 → rule-now / shim-deferred justification), and
`syscall-doctrine.md` (the kernel is the shim every loop calls). Mirror into
`~/.hal/rules/` when a HAL loop earns it.
