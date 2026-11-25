# ADR 0015 -- Improving /converge-fleet: eight decisions, four small fixes first

Date: 2026-10-02 · Status: accepted · Decided by V>> in three `/hitl-decision-matrix`
rounds, accepting every suggested option · Extends `skills/converge-fleet` and its
template `skills/converge-fleet/templates/converge-fleet-workflow-v2.js`.

**In one line:** do four small safety fixes now, eight bigger steps next, and leave
five for later.

## Context -- what the fleet is, and where it breaks

`/converge-fleet` runs a team of AI helpers on one big question. The work is split into
lanes, each helper reads one slice, the helpers pool what they learn between rounds, a
checker tries to refute every finding, and fixes ship through AGORA review.

Two real runs show where it breaks, both recorded in `skills/converge-fleet/SKILL.md`:

- Run `wf_bef8da20-5e1` lost 26 of its 33 agents together and reported 318 claims with
  zero evidence.
- A run on 2026-09-17 spent $303.96 in usage credits. 24 of its 26 verifiers died
  together, and none of its 72 findings was confirmed.

## How the options were found and scored

Nineteen options across eight decisions, each scored 0-5 on four tests V>> chose: trust
(can you rely on the result), cost (cheaper scores higher), watchability (can you see it
work) and reuse (does it help the next run).

Claude Fable 5.1 drafted and scored the options. Astra (GPT-6-Astra, through Codex)
scored them independently and corrected several. A sovereign council of non-Anthropic
models then ruled on the defaults: Grok 4.5 and DeepSeek V4 Pro as speakers, Gemini 3.1
Pro as judge, sealed as `74b4ed4cd9376c5c`. **Only two of the four seats answered, and the
DeepSeek answer was cut off at its 3,000-token limit**, so the ruling is advice from a
reduced quorum, not a full sitting. The council overturned the first draft once: for
"when may a run call itself a success" it chose verified success over a real-world test
as the universal gate.

## Decision

Each decision states what changes and the test that would make us change course. Every
test is registered as a hypothesis (see the table at the end).

### 1. Run record -- record now, scoreboard later

Every run writes a run record with a replay timeline. A Workflow script has no file
access (`lib/fanout_preflight.py:13-17`), so a host-side step writes it. A scoreboard that
compares runs, plus a control run with cross-lane learning switched off
(`TERMS_PER_ROUND=0`), comes once about five records exist.

*Change course if* after five recorded runs the scoreboard has not changed any CONFIG
value. Then keep the record and stop there.

### 2. Messages -- a phone message plus room headlines

A phone message when a run stalls, runs short of budget or finishes. A plain headline in
the mesh room at each phase change, counted as delivered only when a receipt comes back:
`mesh_room.post()` returning a row id (`lib/mesh_room.py:303`) proves storage, not
delivery, which is exactly what run `wf_1dc2a6cb-980` is debugging.

*Change course if* the pings are muted or ignored within three runs, or mesh receipts
never arrive. Then fall back to phone only.

### 3. Helpers -- fixed lane ids now, cheaper readers next

First a permanent `lane_id`, kept apart from the round label (one of the four fixes
below). Then a per-lane routing table: read-only Explore agents for discovery lanes and
broly for synthesis, saving roughly 70k tokens per discovery lane. Codex (Pool C) and HAL
(Pool B) lane types join only after an adapter test proves they can read the repository,
respect privacy limits and report real billing, reusing the sovereign-council Codex
recipe (`lib/sovereign_council.py`) rather than a hand copy.

*Change course if* on the same slice Explore lanes produce fewer findings that survive
verification than broly lanes. Then that lane class goes back to broly.

### 4. Second opinion -- one advisory council sitting before Ship

One sovereign-council sitting as an advisory phase before Ship, about $0.10-0.30 a
sitting, never merge authority. The phase reads each speaker's status in the sealed
record rather than the exit code, because the council CLI exits 0 even when only two of
four seats answer, as happened in the sitting behind this ADR.

*Change course if* over five runs the sitting never changes what ships: no finding
dropped, no claim re-scoped. Then drop it.

### 5. Checker -- every finding first, then open the file

First, the selector sees every finding instead of the first 40, findings left out stay
explicitly UNCHECKED, and calls run in a small parallel pool. Then the verifier opens the
cited location and confirms the quote in place, with literal quotes kept apart from
claims, and private inputs stay on the local route. Part of done: a verifier timeout,
retry and quarantine policy; a redaction bar before any cloud fallback; and
back-pressure so a parallel Verify does not exhaust the quota.

*Change course if* checking in place changes fewer than 5% of verdicts against
quote-only checking on the same findings. Then stop after the first step.

### 6. Success -- verified success

Verdicts are joined onto finding ids, success is recomputed after Verify, Ship
mechanically requires CONFIRMED evidence, and "search finished", "answer established" and
"change authorised" are reported as three separate things. Runs whose goal is an effect
in the world, such as `wf_1dc2a6cb-980`, add an observable outcome test. Findings and
verdict are sealed together as one IDR leaf when output leaves the machine, on a private
chain for personal matters. A seal proves integrity, not correctness.

*Change course if* over ten runs the success recomputed after Verify never differs from
the discovery criterion. Then the recomputation was bookkeeping.

### 7. Budget -- a hard ceiling, then a live quota check

First, an in-run token ceiling with Verify capacity reserved, a per-lane cap on findings,
and dropped questions kept in a bounded backlog recorded as unanswered. Then a live quota
probe in place of the cached billing flag, and rounds that shrink (all lanes, then half,
then one) as the ceiling nears. Durable checkpoints, keyed on source hashes, repository
revision, recipe version and model configuration, come later.

*Change course if* three runs never touch the ceiling and never need to resume. Then keep
only the ceiling and shelve the checkpoints.

### 8. Setup -- a short form now, a replayable package later

A short recipe (question, slices, lanes, criterion, budget) that a builder turns into the
Workflow, shows together with the preflight cost, and launches only after approval. The
Workflow keeps its own JavaScript loop, because the Python kernel `lib/converge.py` is not
reachable from the Workflow runner. Once decisions 1 and 6 exist, a finished run is
packaged with its record and seal so anyone can replay it. Parallel Ship stays deferred.

*Change course if* more than half of the recipes need hand edits. Then the form is the
wrong abstraction.

### Also decided

- **The decision page becomes a GRIP tool.** It covers the plain per-idea Sankey (each idea
  flows into Now, Next or Later), three columns of cards, and the detailed four-test chart
  folded away, with a fit check so no label can be cut off. Retire it if the next three
  decision matrices do not use it.
- **These decisions persist three ways:** this record, one registered hypothesis per
  "change course if" test, and one tracked issue per fix below.

## Now -- the four small fixes

Verified against `converge-fleet-workflow-v2.js` on main, unchanged at `9f908c3c4`:

1. **Verified success.** `satisfied = criterionMet()` runs at `:308`, inside the discovery
   loop and before `phase('Verify')` at `:315`. `:395` returns it as `criterion_met`, and
   Ship at `:371` is gated only by a prompt instruction at `:378`. Astra's probe, with the
   verifier mocked to fail, saw the template still return `criterion_met=true` and invoke
   Ship. In Astra's words, that demonstrates dispatch, not an actual unsafe shipment.
2. **Every finding reaches the selector.** `:324` tells the agent to write "these
   ${strong.length} findings", but `:325` writes `strong.slice(0, VERIFY_CAP)`, with
   `VERIFY_CAP = 40` at `:61`.
3. **An in-run ceiling with Verify reserved.** `lib/fanout_preflight.py:13-17` says a
   Workflow can enforce a token ceiling and should, but no template does. Questions beyond
   `QUESTIONS_PER_ROUND` are dropped at `:264-272`.
4. **A permanent lane id.** Questions raised in the lane phase carry the plain lane name
   (`:236`), but questions raised in a converge round carry the round label
   `r${n}:${lane}` (`:292`). `routeQuestion` (`:185-192`) excludes the asking lane by
   comparing plain lane names with that origin, so from the first converge round the
   exclusion never matches and a lane can be routed its own question.

## Next and later

- **Next:** run record (1), messages (2), cheaper reading helpers (3), the council sitting
  (4), checking in the file (5), the real-world test for world-effect runs (6), the live
  quota check (7), the recipe form (8).
- **Later:** the scoreboard (1), a Pool B mirror lane for the riskiest slice only (4), the
  sealed result when output leaves the machine (6), durable checkpoints (7), the
  replayable package (8).
- **Not chosen:** fixing only the round logs, because the run record replaces it, and
  phone-only messages, because decision 2 includes them.

## Consequences

- The four fixes are each about one line of template code and land first, so the next
  fleet cannot spend heavily and declare success with nothing CONFIRMED.
- The bigger steps each gain a falsifier with a check date, so a step that does not pay
  for itself is removed rather than kept by inertia.
- Decision 4 adds roughly $0.10-0.30 per run from existing provider balances.

## Tracking

The four first fixes are issues: verified success GRIP#6975, every finding reaches the
selector GRIP#6976, the in-run ceiling GRIP#6977, the permanent lane id GRIP#6978.

Each "change course if" test is a registered hypothesis in `learning/hypotheses.jsonl`,
measured by the plan stored with it:

| Decision | Hypothesis | Check by |
|---|---|---|
| 1. Run record | H753: the scoreboard changes a CONFIG value within five runs | 2026-12-31 |
| 2. Messages | H754: pings stay on and room headlines come back with receipts | 2026-11-30 |
| 3. Helpers | H755: Explore lanes match broly lanes on verified findings | 2026-11-30 |
| 4. Second opinion | H756: the council sitting changes what ships within five runs | 2026-11-30 |
| 5. Checker | H757: checking in the file changes at least 5% of verdicts | 2026-11-15 |
| 6. Success | H758: recomputed success differs from the criterion within ten runs | 2026-12-31 |
| 7. Budget | H759: a run reaches the ceiling or resumes within three runs | 2026-11-15 |
| 8. Setup | H760: fewer than half of recipe-built runs need hand edits | 2026-12-31 |

## Provenance

Decided by V>> on 2026-10-02. Options and scores by Claude Fable 5.1 (`claude-fable-5-1`)
and GPT-6-Astra via Codex. Council: Grok 4.5 and DeepSeek V4 Pro, judged by Gemini 3.1 Pro
Preview, seal `74b4ed4cd9376c5c`. Record written by Claude Opus 5.5 (`claude-opus-5-5`).
