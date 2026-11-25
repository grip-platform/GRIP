# Fan-Out Spends the Weekly Limit (PARAMOUNT)

Origin: 2026-10-26. A single `/converge`-style Workflow run, launched to produce
one converged reply to a social-media post, ran to 11 depths and 273
default-type subagents before the operator stopped it. Measured from
`~/.hal/logs/cache-loss-ledger.jsonl`, filtered to that one session and model:

- **864 requests**; `cache_creation_tokens` 90,490,687; `cache_read_tokens`
  103,596,901; `output_tokens` 9,892,488.
- `seven_day_utilization` moved from **0.04 to 0.36** while the run was live,
  and had reached **0.51** a few hours later when this rule was written
  (re-measured directly against the live ledger, not merely relayed).
- Each of the 273 agents carried roughly 237,000 tokens of pure overhead
  before doing any work: `CLAUDE.md` plus the `rules/*.md` stack, plus the
  harness's own relay of the triggering user turn (385 KB, injected
  verbatim into every agent). Council seats run at `effort: max` and each
  produced roughly 50,000 output tokens (one measured: 50,134).
- At the final depth, eight seats made **sixteen requests each over four and
  a half hours** (07:07 to 11:34 UTC) with no assistant turn ever landing,
  `cache_read` at zero on every retry. Each retry re-bought the full ~237K
  of overhead from nothing: a prefix-cache miss, not a cheaper continuation.
- Resuming the run did not recover from this: because the kernel keys its
  cache on content, one failed depth-8 seat made the resume **re-run every
  depth from 8 to 11** against the same cache keys, rather than skipping
  what had already produced a result.
- A one-agent control probe, same schema, `agentType: 'Plan'` instead of the
  harness default, returned in **7 seconds at 66,368 tokens** — roughly 3.6
  times cheaper than the default agent type for the identical judgement.

The operator's own words, relayed when he saw the figures: *"this is a
disaster failure mode... Wasting 32% of the weekly limit on something that is
just a converged reply? ultracode fix immediately, please, GRIP!"* The 32 points
is the 0.04-to-0.36 rise read as a percentage; by the time of the fix the true
figure was 47 points (0.04 to 0.51).

## The law

> **A multi-agent Workflow run declares a hard cap on how many agents it may
> spawn, and is refused outright when the account is already close to its
> weekly or five-hour ceiling. Work that only renders a verdict against a
> fixed schema — no file edits, no tool calls beyond reading its own input —
> runs on a lightweight agent type, never the harness default, and never above
> `effort: high`. Heavy fan-out routes to Pool B or Pool C
> (`rules/pool-a-offload-routing.md`), not to Pool A subagents. A run that has
> stopped producing results is stopped, never waited out. A Workflow is
> launched from a short turn, never from one still carrying the full text of a
> skill or a large relayed payload — that text is what the harness replays
> into every single agent the run spawns.**

Concretely, for anything launched through the `Workflow` tool:

1. **Declare and use a cap.** The script names a `const GRIP_MAX_AGENTS = N`
   as the run's TOTAL agent count across every phase, enforced at every
   `agent()` call site, never a bound on one fan-out alone. No cap, or a cap
   above 48, is refused.
2. **Match the agent type to the work.** An `agent()` call whose job is to
   return a value against a `schema` — no edits, no further dispatch — takes
   `agentType: 'Plan'` or `agentType: 'Explore'`, which skip the ~73K
   `rules/*.md` stack the default agent type always pays
   (`rules/dispatch-economics.md`). The measured ratio here was 3.6x on an
   identical task.
3. **Keep `effort: max` rare and justified.** A judgement-only agent has no
   need of the highest reasoning tier; reserve `max` for work that edits code
   or must survive adversarial review.
4. **Route heavy cognition off the weekly limit.** A council, a deliberation,
   or a bulk classification pass belongs on HAL (Pool B) or Codex (Pool C),
   never on a fan-out of Claude Code subagents, unless the task specifically
   needs the Anthropic voice (`rules/pool-a-offload-routing.md`).
5. **Stop a stalled run; never wait it out.** A run making requests with no
   result landing for an extended period is not "nearly there" — the depth-8
   seats above ran for four and a half hours and produced nothing. Stopping
   early and re-scoping costs less than any amount of waiting.
6. **Launch from a short turn.** Everything in the turn that launches a
   Workflow is replayed into every agent it spawns. A turn still holding a
   skill's full body, a long relayed message, or another large payload
   multiplies that cost by the agent count before any useful work happens.

## Why review-tier failed here (a Class B recurrence)

Two rules already existed that, read and applied, would have stopped this
before it reached depth 2. `rules/dispatch-economics.md` already states the
3:1 economics gate and a depth ladder that starts at depth 0 and escalates
"one rung only when the converge criterion fails... with a documented
reason" — not depth 3 or beyond on the first attempt.
`rules/pool-a-offload-routing.md` already states, as its one-sentence law,
that "a council, review, deliberation, or autonomous/batch job that runs as a
Claude Code subagent... when a HAL Pool-B primitive would do the same work is
a weekly-limit leak."

Both rules were skipped, under the pressure of an explicit instruction to go
deep and converge. That is a **Class B recurrence**: the record already
existed and was violated anyway, not because nobody had written it down, but
because review-tier discipline does not survive contact with an operator's own
urgency in the moment. Per
`memory/feedback_grip_forgets_by_not_binding_not_by_not_learning.md`, writing
a better paragraph is the fix that has already been tried here and has
already failed twice. The correct response to a Class B failure is a
mechanical floor, not a third rewrite of the same prose — which is why this
rule ships with one.

Neither existing rule had a hook bound to the `Workflow` tool. Both were
advisory only. This rule's floor closes exactly that gap, for this one tool.

## The mechanical floor

Two gates, one PreToolUse and one advisory, neither a new blocking layer on
anything except the `Workflow` tool itself:

- **`hooks/workflow-budget-gate.py`** (PreToolUse, matcher `Workflow`) calls
  `lib/workflow_budget_gate.py::decide`. It DENIES a launch with no declared
  `GRIP_MAX_AGENTS` cap, a cap above 48, or a weekly/five-hour utilisation
  already past a ceiling (0.70 weekly, 0.85 five-hour). It WARNS, allowing the
  launch, when weekly utilisation is past 0.50 or the script shows bloat
  signals (a schema-only `agent()` call with no lightweight `agentType`, or an
  `effort: 'max'` seat) — printing the estimated token cost of the declared
  agent count at the heavy and the lightweight rate. An explicit, reasoned
  override (a `// grip-budget-override: <reason>` comment of twenty characters
  or more, or `GRIP_WORKFLOW_BUDGET_GATE=0`) always allows, with the reason
  logged. Every decision is audited to `logs/workflow-budget-gate.jsonl`.
- **`hooks/spend-watchdog-hook.py`** (UserPromptSubmit, advisory, never
  blocks) calls `lib/spend_watchdog.py::assess` for the current session. It
  scans every workflow run's `journal.jsonl` for one that is in flight
  (more agents started than have produced a result or a failure) and has
  produced no result for thirty minutes while its in-flight agents have each
  made three or more API requests in that window — the exact shape of the
  depth-8 stall above — and separately checks whether this session has spent
  five million tokens, or moved the weekly figure by five points, in the last
  hour. It prints at most one line, naming the run and the fix ("stop it"),
  and exits 0 whether or not it has anything to say.

**Escape-hatch audit** (`rules/gate-resolution-escape-hatch.md`, Invariant 1):
the only thing `workflow-budget-gate.py` can deny is one `Workflow` launch. Its
resolution — edit the script to add or lower the cap, swap the agent type, or
wait for the window to reset — is never itself a `Workflow` launch, so the
gate cannot block its own resolution. `spend-watchdog-hook.py` has no deny
path at all; it can be ignored but never blocks anything.

Neither gate ever engages the paid key. A weekly or five-hour ceiling is
crossed by waiting for the reset
(`rules/claude-cap-moves-to-the-next-subscription.md`), never by setting
`GRIP_CLAUDE_ALLOW_PAID=1`.

## Falsification

Wrong if, over the next ~20 Workflow launches this gate sees:

- The declared-cap or agent-type checks block a run that genuinely needed the
  default agent type and a cap above 48, more often than they catch a real
  repeat of this incident — then the ceiling of 48 or the agent-type
  requirement is mistuned; raise it rather than dropping the check.
- A run this gate allowed still burns more than ten weekly points in one
  sitting — then the ceilings (0.70 weekly, 0.85 five-hour, 0.50 warn) are set
  too high and should be lowered.
- `spend_watchdog`'s STALL verdict fires on a run that was genuinely about to
  land a result — then thirty minutes and three requests per agent is too
  tight; widen it before removing the check.
- The override is used routinely rather than rarely — then either the cap is
  wrong for real work, or the override's twenty-character reason bar is too
  low to make someone think before reaching for it.

Track: weekly-utilisation points moved by a single Workflow run (target under
10; this incident moved at least 32, and 47 by the time it was measured
again), and denied launches that were re-tried successfully after the stated
remedy (target: all of them).

## Scope

Every launch of Claude Code's `Workflow` tool, in every GRIP session on this
machine. Sits beside `rules/dispatch-economics.md` (the pre-dispatch economic
gate this rule gives a hook to), `rules/pool-a-offload-routing.md` (where
heavy cognition belongs instead), `rules/gate-resolution-escape-hatch.md` (why
this gate can never deadlock a session), `rules/claude-cap-moves-to-the-next-subscription.md`
(what to do at a real ceiling), and
`rules/measured-claims-read-the-output-first.md` (every number in this rule
was read from the ledger it describes, not typed from memory). Shim:
`lib/workflow_budget_gate.py` · `hooks/workflow-budget-gate.py` ·
`lib/spend_watchdog.py` · `bin/grip-spend-watchdog` ·
`hooks/spend-watchdog-hook.py` · `tests/test_workflow_budget_gate.py` ·
`tests/test_spend_watchdog.py`.
