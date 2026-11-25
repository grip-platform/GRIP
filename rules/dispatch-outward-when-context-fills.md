# Dispatch Outward When the Main Session Fills (PARAMOUNT)

Origin: 2026-08-09, V>>: *"launch a pool B and C background agent team as we are at 85%
of main session context window (btw this should probably become a rule — to launch in
background agents, when the main session is near full)."* Rule-at-N=1
(operator-declared).

## The law

> **As the main session approaches its context ceiling, remaining work moves OUT to
> background agents with fresh context windows — it does not get crammed into the
> last few percent, and it does not stop.**

The main session's scarce resource near the ceiling is not time or capability, it is
**context**. A background agent starts at zero. Work that would cost the last 10% of a
full window costs a background agent almost nothing, and the main session keeps the one
thing an agent cannot inherit: the accumulated judgement of the whole session.

## Why this is structural, not just tidy

GRIP's context gate is its **only total-block gate** — at 85% it denies every tool call.
`rules/gate-resolution-escape-hatch.md` records the day that deadlocked a live session:
the gate denied the very `Edit` that would have resolved the blocked condition, and an
external human had to intervene.

**Dispatching outward is the structural escape from that deadlock.** Instead of fighting
the ceiling from inside — compacting, self-editing, negotiating with the gate — the work
leaves the window entirely. Nothing needs to be relaxed and no gate needs touching.

This is the same move `rules/loop-engineering.md` makes with recursion depth: when the
current frame is exhausted, you do not grow the frame, you spawn a new one.

## When to fire

Fire **before** the ceiling, not at it — a dispatch itself costs context, so a session
that waits until 90% may not afford the briefs it needs to write.

- **~70–80%** — start routing new parallelisable work outward by default.
- **~85%** — dispatch everything that can leave; keep only synthesis and the operator
  conversation.
- **Immediately, at any level** — when the operator says the window is filling. Their
  read of the session beats any threshold.

## What goes out, and what must stay

**Dispatch outward** — work that is self-contained, parallelisable, and produces a
**concrete artefact**: a code fix behind a PR, a read-only audit with an evidence table,
a research sweep, a test-writing pass, a scan across many files.

**Keep in the main session** — anything that depends on context an agent cannot be
handed cheaply: the operator dialogue, the decision that weighs tonight's accumulated
evidence, the synthesis that turns several agent reports into one answer, and any
irreversible or operator-gated action.

The discriminator: *could I write this brief in a paragraph?* If yes, dispatch it. If the
brief would need the whole session's history to make sense, it stays.

## Non-negotiables (inherited, not restated)

- **The economic gate still applies** (`rules/dispatch-economics.md`) — near-full context
  justifies dispatching work that was *already* worth doing. It never justifies inventing
  work, nor fanning out where one agent would do.
- **Every agent must return a concrete artefact** (`rules/agent-dispatch-protocol.md`) —
  intent prose in a terminal turn is not a deliverable.
- **Verified evidence travels in the brief, never a raw status**
  (`verify-canonical-not-claimed.md`). A brief built on an unverified premise launders it
  into N parallel contexts at once, which is worse than one wrong session.
- **Merge gates are unchanged** — an agent's PR still needs AGORA PASS + green CI. Context
  pressure is never a reason to lower a bar.

## Falsification

Wrong if, over the next ~10 near-ceiling sessions: dispatched agents routinely return work
the main session must redo because the brief could not carry enough context (then the
discriminator above is mis-drawn — tighten what qualifies, do not drop the rule); OR
dispatching at 70–80% consistently spawns agents for work that turns out not to be needed
(fires too early — move the threshold up); OR sessions that ignore this rule and simply
compact reach the same outcome for less total token spend (then compaction is the better
tool and this is ceremony). Track: sessions that hit a hard context stop with work still
undispatched (target 0); agent output discarded as unusable (target low).

## Scope

Every session, every machine. Sits beside `rules/gate-resolution-escape-hatch.md` (the
deadlock this avoids), `rules/dispatch-economics.md` (whether to dispatch at all),
`rules/agent-dispatch-protocol.md` (the structural contract), and the `/ultraclear`
skill (the complementary move — partition and cheap-clear when the work must stay in one
line of thought rather than fan out).
