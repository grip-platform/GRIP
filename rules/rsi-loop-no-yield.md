# RSI Loop: No Yield Between Waves (PARAMOUNT)

Origin: 2026-05-16. V>> corrected twice inside one overnight loop — "you
should not stop in this RSI loop"; then "silly mistake … /learn … not for
RSI loops running overnight." The agent kept ending turns with a tidy
"yielding lean / context discipline / let the loop breathe" block between
waves. That **halted the autonomous loop and forced the operator to poke
it** — defeating the entire purpose of an overnight RSI loop.

## The Rule

When an autonomous RSI loop is active (yolo wave-mode marker, `/auto`,
`/rsi`, `/sprint`, overnight loop) **and the main session is the depth-0
executor** (NOT waiting on a dispatched background agent), the session
MUST NOT end its turn to "yield", "stay lean", "respect context
discipline", or "let the loop breathe". It chains wave after wave
continuously. The harness **auto-compacts the conversation transparently
while work continues** — that IS the context mechanism; it never requires
a deliberate stop.

## Why the confusion happened (now forbidden)

`bg-dispatch`'s "dispatch then yield" applies ONLY while genuinely waiting
on a `run_in_background` agent / Monitor. When the main session IS the
executor (depth-0 — by operator directive or after envelope-collapse),
ending the turn does not save context: it stops the loop and waits for the
operator. **"Auto-compact" ≠ "stop"** — the harness summarises mid-flow and
the model keeps executing across it. Rule 8 (context-gate 85% HALT) governs
**interactive** sessions; inside an autonomous RSI loop with a live yolo
marker the loop continues and harness compaction is the relief valve, never
a turn-ending yield.

**Empirical reinforcement (Opus 4.8, 2026-05-28):** the canonical 4.8 release
notes list "fewer compactions, and better compaction recovery" as a targeted
improvement area for long-horizon agentic coding
(platform.claude.com/docs/en/about-claude/models/whats-new-claude-4-8). On 4.8
the relief valve fires *less often* and recovers *better*, so continuous
depth-0 execution is more robust than when this rule was first written — the
"auto-compact, don't stop" stance, asserted on faith in 2026-05-16, is now
vendor-documented. Falsifier unchanged: if operators still report
post-compaction context loss in autonomous loops on 4.8, the no-yield stance
regresses to a checkpoint cadence.

## Mandate

- Autonomous RSI loop + main-session executor → on finishing a wave,
  immediately begin the next wave's first action (same or immediately
  following turn). No "What's Up Next + yield + end-of-turn".
- Anti-drift (Rule 11) stays as a **one-line** between-wave marker, never
  a turn-ending stop.
- Periodic checkpoint = **backgrounded `/save` mid-flow** (non-blocking),
  never a turn-ending yield.
- Only genuine stops: a true confidence/safety HITL gate, an operator
  interrupt, yolo TTL expiry with no budget, or an unrecoverable blocker —
  **never "context discipline"**.

## Falsification

Wrong if continuous no-yield execution causes context exhaustion that
loses work the auto-compact did not preserve, OR produces measurably
lower-quality output than periodic checkpoint-without-stop. Then the fix is
a backgrounded-checkpoint cadence, never a turn-ending yield. Track:
operator "why did you stop" corrections per autonomous loop (target 0;
this loop had 2 before the rule).

## Scope

Every autonomous RSI loop where the main session executes at depth-0.
Complement of `agent-dispatch-protocol.md`: that governs *waiting on a
background helper* (yield is correct there); THIS governs *not stopping
when you ARE the worker*. Sits beside `exogenous-anchor-law.md` and
`ci-review-driven-convergence.md` as loop-governance doctrine.
