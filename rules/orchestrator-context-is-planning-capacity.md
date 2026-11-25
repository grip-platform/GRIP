# Orchestrator Context Is Planning Capacity (PARAMOUNT)

Origin: 2026-09-10. V>> reviewed the eigenwise.io article *"Using GPT-6 Astra inside
Claude Code is the new meta"* and the setup behind it (an orchestrator over Opus/Fable
builders with blind cross-vendor reviewers). Its one discipline worth stealing: the main
session is a pure orchestrator that never reads files, because its context window is the
scarcest resource in the whole arrangement. Every file it reads "just to do this one
quickly" is planning capacity it no longer has. It should dispatch, read reports, decide.

Evidence from session `7cf9ad38` (2026-09-07/08, the session that dispatched this rule)
is measured below, not asserted — and the measurement is smaller than the habit felt:
16 direct reads while an agent that later reported was out, and 20 of 36 dispatches whose
report the main session never read. The rule is written to what the numbers support.
Rule-at-N=1 (operator-chosen). Measurement shipped alongside. No gate, for reasons given
under Enforcement.

## The law

> **The main session's context window is planning capacity, not a reading budget. While
> agents are out, the orchestrator dispatches, reads their reports, and decides. It does
> not read the world itself.**

## Why this is a separate rule and not a restatement

`rules/dispatch-outward-when-context-fills.md` is a **threshold** rule: at 70-85% of the
window, remaining work leaves. `rules/dispatch-economics.md` decides **whether** a piece of
work is worth an agent at all. Neither governs the turn-by-turn choice that empties the
window long before any threshold is reached — the accumulation of individually cheap reads,
each one defensible, made while an agent was already out reading for the same purpose. By
the time the threshold rule fires, the capacity those reads consumed is gone, and
compaction gives back a summary, not the capacity.

This is not a token-economics claim, and it must not be read as one. Dispatch-economics is
right that the main thread has its ambient floor loaded, so the *marginal token cost* of a
read there is near zero. The resource this rule protects is different: the remaining
window of the ONE session that holds the operator dialogue and the accumulated judgement.
A file read into an agent costs tokens; the same file read into the orchestrator costs
tokens **and** planning capacity. Same price, different currency.

## What the baseline shows, and what it does not

Measured with `python3 lib/orchestrator_budget.py <transcript>` over session `7cf9ad38`
(Claude Code 2.1.261, Fable 5.1, 1M window; 941 main-session records, 237 API requests,
peak context 913,464 tokens). Token figures are chars/4 estimates; the ratios do not depend
on that constant (tested).

| Measure | Baseline |
|---|---|
| Direct reads by the main session (Read/Grep/Glob + read-shaped Bash) | 83 calls, ~59k tokens |
| Dispatch (36 background launches + 4 TaskOutput; briefs + reports + 21 notifications) | 40 calls, ~118k tokens |
| Reading : dispatch | **0.50** |
| Reading as a share of all tool-driven context | 19% |
| Reads while any dispatch was unresolved at that moment (loose) | 79 of 83, ~41k tokens read back |
| **Reads while a dispatch that later returned its report was in flight (strict)** | **16 of 83, ~2k tokens read back** |
| Reads with nothing in flight | 4 calls, ~5k tokens |
| Bash-shaped reads vs the Read tool | Bash(read) 58 calls / ~34k; Read 25 calls / ~25k |
| Background dispatches with no report read back by the end of the transcript (paid-for reports never read by the main session) | 20 of 36 |

Be exact about what this does and does not show, because the first draft of this rule got
it wrong. Reading was **not** the largest tool-driven term in that window: the dispatch side
was twice as large, and Edit/Write/mutating Bash larger still. And the 79-of-83 figure, which
the first draft carried as its headline, is mostly an artefact: under the loose definition,
once one dispatch goes unresolved every later read counts as "in flight", and 20 of the 36
dispatches never resolved. The truncation-robust count — reads made while a dispatch that
*did* return its report was out — is **16 of 83, about 2k tokens**. That is the redundant
class this rule targets, measured honestly, and in this session it is small. An AGORA seat
(xAI Grok 4.3, PR #5420) named the falsifier before the number existed; the strict count was
added as its forward anchor and the headline corrected.

The larger finding is the mirror image: 20 of 36 dispatches whose report the main session
never read. A dispatch counts as unresolved when neither a `<task-notification>` carrying its
`<tool-use-id>` nor a `TaskOutput` result with a terminal `<status>` for its task id appears
later in the transcript. The transcript carries session-limit auto-resumes and seven
"background work no longer running" check-ins, so some of those agents ended without a report
and some may still have been running when the transcript ends; the tool cannot separate a dead
agent from a completion shape it does not recognise, and says so. What the 63 reads between
the two counts represent — the orchestrator compensating for silent agents, or reading while
paid-for reports went unread — is therefore not established.

The Bash line is the operational lesson that survives either reading: `sed -n`, `rg`,
`git log`, `gh pr view` are reads; the tool name does not launder them. In this session they
outweighed the Read tool itself. Heredoc scripts are unclassified and land in `other`, so the
reading figure is a lower bound.

## Mandate

1. **Orchestrator posture means at least one dispatched agent or workflow is in flight.**
   In that posture the main session does not read the world. It waits for the report, asks
   the running agent (`SendMessage`), or spawns a floor-lean `Explore` for a genuinely new
   question. The tell is the phrase "just to check quickly".
2. **Reads that a brief needs are planning; keep them to the brief's shape.** A directory
   listing, a signature, a line count — never the body. Name the path in the brief and let
   the agent read the body. If the read would cost more than the brief, it belongs in the
   brief.
3. **Reading an agent's report is the orchestrator's job.** That spend is what the window is
   for, and the measurement books it on the dispatch side deliberately. Not reading it —
   the 20 of 36 above — is the failure, not the reading.
4. **Depth-0 work with nothing in flight is governed by dispatch-economics, not this rule.**
   A 40-line read is cheaper than an 80k-floor general-purpose agent and this rule does not pretend
   otherwise. It binds on posture, not on an absolute prohibition. The article's absolutism
   is the right instinct and the wrong law for a harness with a measured ambient floor.
5. **Bash counts.** A read is a read whichever tool performs it; classify by what the
   command does, and prefer the agent that is already out over a new `rg` in the main window.

## Enforcement tier (knowledge-maturation-functor)

**Rule now. Measurement now. No gate, and no advisory hook yet.**

- `lib/orchestrator_budget.py` is a measurement, not a gate: it reads a session transcript,
  skips subagent records, tracks background dispatches in flight until their notification
  lands, tags every read with whether an agent was out at the time, and reports the buckets
  above. Tests: `tests/test_orchestrator_budget.py` (57; each observed red first; the
  sidechain filter, the in-flight resolution and the estimator invariance re-proved red
  under named mutations).
- **A gate on reading is refused by construction**, not deferred. Denying Read/Grep/Glob/Bash
  denies the operations that resolve almost every blocked condition GRIP has — it fails
  `rules/gate-resolution-escape-hatch.md` Invariant 1 on its face, the same shape as the
  2026-06-08 context-gate deadlock.
- **An advisory Stop-hook is not shipped either.** N=1 does not earn a shim under the functor,
  and the binding would live in `settings.json`, which is an operator step. Revisit at N=2
  with this tool's output over two independent sessions as the evidence. If it is earned,
  the shape is a WARN-only Stop line printing the in-flight read count — it denies nothing,
  so it passes the escape-hatch audit trivially.

## Falsification

Wrong if, over the next ~10 orchestrator sessions (three or more dispatches):

- the strict in-flight read count does not fall below its 16-of-83 baseline while the rule
  is loaded, and unread dispatch reports do not fall from 20 of 36 — then the rule is ceremony
  and the leak is elsewhere; re-measure with the tool before writing a fourth rule about
  context;
- sessions that obey it produce synthesis the operator has to redo because the orchestrator
  lacked a fact no agent report carried — then mandate 2's carve-out is drawn too narrow;
  widen what counts as a planning read, do not drop the posture law;
- the in-flight read count falls and the window still fills at the same rate — then reading
  was never the operative term (briefs, plan echoes and hook injections are), this rule
  demotes to advisory, and `/context-forensics` names the real one. The baseline already puts
  reading at 19% of tool-driven context and the strict count at 2k tokens, so this is the
  clause most likely to fire; it is kept rather than pre-empted because the rule's claim is
  about posture, not volume;
- calibrating the chars/4 estimator against API usage moves either ratio by more than 2x —
  then fix the estimator; the ratio, not the absolute figure, is the claim.

Track, per orchestrator session, with `lib/orchestrator_budget.py`: the strict in-flight read
count (baseline 16/83), with the loose count beside it so the gap stays visible (79/83);
reading : dispatch (baseline 0.50); unresolved dispatches at end (baseline 20/36 — paid-for
reports never read). Never track total read count as the metric: a session with no agents out
and forty reads is depth-0 work and is fine.

## Scope

Every session that has dispatched at least one agent or workflow, on every machine. It
belongs in the `extended` (main-session-only) set of `config/rules-scope.json`: a subagent is
the reader this rule wants working, and loading it into subagents would tell the reader not
to read. Sits beside `rules/dispatch-outward-when-context-fills.md` (the threshold),
`rules/dispatch-economics.md` (whether to dispatch at all), `rules/agent-dispatch-protocol.md`
(the structural contract), the `bg-dispatch` skill (dispatch, then yield — this rule is the
per-turn discipline that skill assumes), the `/context-forensics` skill (where the rest of
the window went), `rules/gate-resolution-escape-hatch.md` (why there is no gate) and
`rules/knowledge-maturation-functor.md` (why there is no shim yet). Measurement:
`lib/orchestrator_budget.py` · `tests/test_orchestrator_budget.py`.
