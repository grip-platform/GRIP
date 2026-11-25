# Congruent Continuity — Build Log, 2026-08-30

This is the evidence-anchored account of the night the fourth moat leg went from
an idea in the operator's head to a live, signed, verified chain. Every claim in
this log names its anchor: a commit, a file and line, a record id, or a command
with its output. Where something is still pending, this log says so plainly —
two of the night's own failures are recorded here in full, because they are live
specimens of exactly the failure class this moat leg exists to catch.

Timestamps: git commits are quoted as recorded (+02:00 local); chain records and
workflow state carry UTC. Both appear below, converted where it helps.

---

## What tonight built, in one paragraph

Congruent Continuity records the reasoning path between what GRIP believes and
what it decides, as signed, pre-registered falsifiable commitments — "I am about
to act because I believe X; here is what would prove X wrong" — with exogenous
verdicts settled back into the same chain (`CONGRUENCE.md:21-38`). Tonight the
genesis plan was locked with the operator, the polyglot spec and doctrine were
written, the recorder and the deterministic settlement floor were implemented
and tested, three hypotheses were put on the clock, and the first live
commitment was appended to the chain — a bet on the night's own ideation
workflow, which is still running as this log is written.

## The night, in order

| Time (UTC) | Event | Anchor |
|---|---|---|
| 00:30:58 | Genesis plan committed, HITL-locked (four decisions D1-D4) | commit `fc64e562`; `docs/congruent-continuity-genesis.md:75-82` |
| ~00:46 | First launch of the W1 ideation workflow dies instantly: `args` never reached the script | transcript TypeError, quoted below |
| 00:46:49 | Relaunch with args intact — run `wf_a7351432-993` starts | run state `startTime: 1788050809672` |
| 00:51:22 | Recorder committed: `lib/continuity_chain.py` + 8 tests | commit `70e5931f` |
| 00:51:25 | First live commitment appended to the chain (the dogfood bet on the workflow) | chain record `precog-1788051085-9aab9e0f` |
| 00:56:15 | CONGRUENCE.md spec + `rules/congruent-continuity.md` doctrine committed | commit `6d432771` |
| 00:56:17 | H719, H720, H721 registered against PR 5140 | `~/.grip/learning/hypotheses.jsonl` |
| 01:13:11 | Settlement floor committed: `lib/continuity_settle.py` (`probe:` convention) | commit `5bd001e5` |
| 01:13:53 | Test fix + on-record correction of a masked suite count | commit `e0894898`, quoted below |
| ~01:16 | Run `wf_a7351432-993` killed after 29m38s — 121 lanes dead on the same usage-limit error | run state `status: "killed"`, `durationMs: 1778116` |
| 01:24-01:28 | Conflict-marker repair; AGORA round-5 concerns addressed on PR 5140 | commits `8fa6e358`, `5da0aed6` |
| later | W1 ideation retry `wf_336cb6d0-609` live; six-lane build fleet `wf_20ed3f89-fe4` launched (this log is one of its lanes) | run directories under the session's `subagents/workflows/` |

## What shipped on the branch

`git log --oneline origin/main..HEAD` in this worktree returns eight commits:

```text
5da0aed6 docs+test: address AGORA round-5 concerns on PR 5140
8fa6e358 fix: remove unresolved conflict markers from system-capabilities
e0894898 fix(tests): injection test must exercise the allowlist path, not metachars twice
5bd001e5 feat(continuity): settlement worker deterministic floor (probe: convention)
dbda61f0 chore: regenerate capability stats after new rule
6d432771 docs(spec): CONGRUENCE.md polyglot spec + congruent-continuity doctrine
70e5931f feat(continuity): Congruent Continuity recorder — signed commit/settle chain
fc64e562 docs: Congruent Continuity genesis — fourth moat leg, HITL-locked
```

The delta (`git diff --stat origin/main..HEAD`) is ten files, 1,337 insertions,
1 deletion: the spec (`CONGRUENCE.md`, 197 lines), the genesis plan, the
recorder (`lib/continuity_chain.py`, 375 lines), the settlement floor
(`lib/continuity_settle.py`, 170 lines), the doctrine
(`rules/congruent-continuity.md`), three test files (316 lines between them),
a one-line `.gitignore` allowlist negation, and the regenerated capability
stats line.

The work rode PR [5140](https://github.com/grip-platform/GRIP/pull/5140)
through five AGORA review rounds. Round 5's blocking concern was refuted with
evidence and answered with a forward-anchor test anyway, and its two
documentation concerns were accepted and fixed (commit `5da0aed6`'s message
records all three dispositions). As of the last evidence recorded locally, a
merge watch was armed on green; this lane works local-reads-only and has not
probed GitHub, so the PR's final state is deliberately not asserted here.

The suite count grew 8/8 → 13/13 → 20/20 → 21/21 across the night's commit
trailers. Rather than trust the last trailer, this log re-ran the three suites
while being written:

```text
$HOME/.claude/venv/bin/python3 -m pytest tests/test_congruence_spec.py \
    tests/test_continuity_chain.py tests/test_continuity_settle.py -q
21 passed, 30 warnings in 6.95s
pytest_exit=0
```

(The warnings are pytest temp-directory cleanup noise on old garbage dirs,
not assertions.) The tests prove they can go red: the tamper test flips the
chain verdict from VERIFIED to BROKEN on a one-word unsigned edit, and the
injection test proves a `; rm` payload is refused unexecuted (commit messages
`70e5931f`, `5bd001e5`).

## The chain is live

The recorder's first real record is in the live chain
(`~/.grip/state/precog/continuity.jsonl`, one record, 846 bytes). It is a bet
on the night's own workflow, appended at depth 0 directly on the human genesis
anchor:

- **id**: `precog-1788051085-9aab9e0f`, **ts**: `2026-08-30T00:51:25Z`
- **content_addr**: `sha256:6c9127a87840f3ef09f0ec68ff8c55841af75e7b5d10003bc14211bbc71fefa8`
- **claim**: "W1 ideation workflow wf_a7351432-993 yields >=3 refute-surviving
  gentner-gated predictors and a non-empty synthesis design"
- **falsifier**: "workflow completes with survivors < 3, or fails, or synthesis
  design_md is empty"
- **deps**: `[]` · **severity**: `reversible` ·
  **goal_ref**: `overnight-congruent-continuity-build-2026-08-30`
- **predecessor_idr**: `human:congruent-continuity-hitl-2026-08-30`
  (the operator's four locked HITL decisions, `lib/continuity_chain.py:74`)

Two honest timing notes. First, the commitment was appended three seconds after
the recorder's own commit landed (00:51:22 → 00:51:25) — the tool notarised its
first bet the moment it existed. Second, it postdates the workflow's launch by
four minutes and 36 seconds: it is pre-outcome (the run was killed 25 minutes
later, and its retry has not finished), not pre-launch. The append-before-act
invariant this chain exists for binds commitments to their *intended actions*;
here the gated action is acting on the workflow's synthesis, which has not
happened yet.

The worklist and the integrity check, run while writing this log:

```text
$ PYTHONPATH=<worktree> python3 -m lib.continuity_chain unsettled
{"content_addr": "sha256:6c9127a87840f3ef09f0ec68ff8c55841af75e7b5d10003bc14211bbc71fefa8",
 "claim": "W1 ideation workflow wf_a7351432-993 yields >=3 refute-surviving
 gentner-gated predictors and a non-empty synthesis design"}

$ PYTHONPATH=<worktree> python3 -m lib.continuity_chain verify
VERIFIED
exit=0
```

One observation the settlement worker will have to face: the claim's wording
pins the first run id (`wf_a7351432-993`), and that run was killed; the retry
resumes its cached work under a new id (`wf_336cb6d0-609`). Settling this
commitment honestly means reading the claim against that lineage — a real,
recorded example of why verdicts need evidence fields rather than a boolean.

Settlement, when it comes, is fail-closed on write: a verdict naming a
commitment absent from the chain raises `ValueError` — "verdicts must not
dangle" (`lib/continuity_chain.py:199-200`). The deterministic floor settles
only falsifiers of the form `probe: <cmd>`, run without a shell against a
read-only first-token allowlist of nine commands
(`lib/continuity_settle.py:51-54`), 30-second timeout, metacharacters refused
and recorded as UNVERIFIABLE. This commitment's falsifier is prose, not a
`probe:`, so it waits for the L2 exogenous council — which does not exist yet
(see "What is not done").

## Three hypotheses on the clock

Registered 2026-08-30T00:56:17Z in `~/.grip/learning/hypotheses.jsonl`, all
carrying `pr_number: 5140`, all `status: pending`:

- **H719** (deadline 2026-08-31): the live chain records >= 5 commitments with
  >= 3 settled on deterministic evidence. Reality at log time: 1 commitment,
  0 verdicts. The settlement floor exists precisely to close this; the gap is
  stated here so the deadline check cannot be graded on vibes.
- **H720** (deadline 2026-09-06): post-hoc settlement localises >= 50% of
  sampled INCONGRUENT sessions to a specific divergence commitment.
- **H721** (deadline 2026-09-06): recording plus checking overhead stays under
  10% of session token budget with Pool-B checkers.

These are the genesis plan's H-CC-2 and H-CC-3 nearly verbatim
(`docs/congruent-continuity-genesis.md:144-154`); H719 tightens H-CC-1's
30-day watcher claim into a next-day recorder-throughput claim.

## Two live specimens of the failure class

The night supplied its own demonstrations of the exact defect Congruent
Continuity targets: a load-bearing premise adopted without a check, false, and
silently poisoning everything downstream.

**Specimen 1 — the workflow args drop.** The first launch of the W1 ideation
workflow assumed the premise "the script's declared `args` reach the script".
Nothing checked it. The Workflow call omitted `args` entirely, and the script's
very first interpolation (`${args.worktree}`, staged script line 36) killed the
run in milliseconds:

```text
TypeError: undefined is not an object (evaluating 'args.worktree')
    at <anonymous> (workflow.js:26:12)
```

The session's own contemporaneous record calls it what it is: "The workflow
failed immediately because I referenced `args.worktree` in the script's template
interpolation without actually passing `args` into the Workflow call" (main
session transcript). This is `rules/declared-input-must-reach-the-actor.md` at
runtime — and it is the happy variant, because it failed loudly. Had the script
defaulted instead of thrown, every lane would have read the wrong tree and
produced coherent nonsense. A continuity commitment on that premise, with
falsifier `probe: test -d <worktree>` settled before launch, is the cheap
version of the check that did not happen. The relaunch carried
`args: {"worktree": "<this worktree>", "gridlm": ".../examples/gridlm"}` (run
state, `args` key) and ran for half an hour.

**Specimen 2 — the pytest pipe that masked a red exit.** Commit `5bd001e5`
shipped with a trailer claiming 20/20 tests. The premise "the suite's exit code
reached the gate" was false: the run was piped through `tail`, the pipeline
reported `tail`'s exit status, and a genuinely red test (19/20) sailed through
the push gate. The correction is on the record in the next commit's message
(`e0894898`):

> CORRECTION: the previous commit's trailer claimed 20/20; it was 19/20 at
> commit time — a pytest exit code masked by a tail pipe let the push gate
> pass. This commit's count is from an unpiped run.

The failing test itself was a second instance of the same class one level down:
it meant to prove the allowlist refusal but used a payload whose parentheses
tripped the metacharacter screen first, so it never exercised the path it
claimed to test (`e0894898`'s subject line). Both layers are now fixed and the
count above (21 passed, `pytest_exit=0`) is from an unpiped run by
construction.

These two are kept in this log deliberately. They are not embarrassments to
bury; they are the calibration corpus's first entries — the shapes the
settlement worker and, later, the watcher must catch.

## The rate-limit cascade and the retry

Run `wf_a7351432-993` (the relaunch with args intact) fanned out the ideation
lanes and then hit a wall. The numbers, each from a named artefact under the
session's workflow state (`~/.grip/projects/<slug>/<session>/`):

- The run's journal (`subagents/workflows/wf_a7351432-993/journal.jsonl`)
  counts **166 started, 121 failed, 39 result** events; 166 lane transcripts
  sit beside it on disk.
- The run state (`workflows/wf_a7351432-993.json`) shows `agentCount: 161`
  with per-lane states **39 done, 121 error, 1 progress** (the five extra
  journal starts have no progress entry — lanes caught by the abort boundary).
- All 121 errors are the **same string**: "You've reached your Fable 5 limit.
  Run /usage-credits to continue or switch models with /model." (census over
  the run state's error fields: `121x`, no other error text).
- The runner retried into the cascade — six lane entries carry a
  "(retry N)" label; the sampled errored lane `diverge:cfiof-thesis (retry 2)`
  died on `attempt: 3`.
- The run ended `status: "killed"`, `error: "Workflow aborted"`, after
  `durationMs: 1778116` (29m38s), i.e. roughly 01:16 UTC.

Context for the wall: the account had already been running on failover for
days. The marker at `~/.grip/state/session/auth-failover.json` reads, verbatim
(timestamps carry no timezone in the file):

```json
{"engaged_at": "2026-08-26T01:55:09", "by": "grip-auth-failover --note-cap (auto)",
 "bucket": "api-key", "entry": "grip-anthropic-external",
 "reason": "usage-cap on subscription -> failed over to API-key bucket",
 "last_cap": "2026-08-29T19:11:54", "cap_reset": ""}
```

So: subscription bucket capped and failed over to the API-key bucket since
2026-08-26, with the most recent cap event recorded hours before the run. The
121 failures are real usage-limit rejections, not synthetic test fixtures — the
word "synthetic" in tonight's working notes refers to the cascade
manufacturing failure volume (retries consuming fresh lane slots against the
same closed gate), and this log prefers the measured description.

The main session diagnosed the cause, fixed the routing, and relaunched: "W1
retry is LIVE (run `wf_336cb6d0-609`) with the failure cause fixed" (main
session transcript). The retry reuses the same staged script
(`cc-w1-predictor-ideation-wf_a7351432-993.js`) with the first run's cached
results, and its journal already carries candidate results from 15+ lanes. It
had not finished when this log was written — a running workflow writes its
state json only at the end, and none exists yet for `wf_336cb6d0-609`.

## What is not done (say so plainly)

- **The W1 predictor-stack synthesis is pending.** The retry is mid-flight;
  no synthesis artefact exists yet, and the dogfood commitment stays on the
  unsettled worklist until it does. The predictor contract in
  `CONGRUENCE.md:137-151` (CF-IOF vs Naive Bayes vs Logistic Regression, with
  abstention) is a stated contract, not a benchmarked result.
- **Watcher precision is unmeasured.** The pre-hoc PRECOG watcher does not
  exist; per locked decision D3 it arms only after the post-hoc corpus measures
  checker precision (`docs/congruent-continuity-genesis.md:81`,
  `CONGRUENCE.md:14-17`). Nothing in tonight's work claims a live brake.
- **The L2 exogenous council is not built.** The deterministic floor is merged;
  prose falsifiers — including the chain's only current commitment — have no
  settler yet.
- **The live chain has zero verdicts.** H719 requires >= 5 commitments and
  >= 3 deterministic settlements by 2026-08-31; the distance is stated above.
- **PR 5140's final state is not asserted from this lane** (local reads only);
  the last local evidence is round-5 concerns addressed and a merge watch
  armed.

## How this log was assembled

All evidence gathered read-only from this worktree and the operator's local
state — no network, no writes outside this file. Commands: `git log` /
`git diff --stat` / `git show` over `origin/main..HEAD`;
`python3 -m lib.continuity_chain unsettled` and `verify` (exit captured
unpiped); `cat` of the live chain and failover marker; `jq` censuses over the
workflow run state and journal; `rg` over the staged workflow script and the
session transcript for the two specimens' verbatim records; and an unpiped
pytest re-run of the three continuity suites.

---

V>> (Lourens Cornelius "Laurie" Scheepers)
___
With assistance of
  Claude Fable 5 (claude-fable-5) — evidence gathering, verification runs, this document


## The recovery arc (appended 03:36 UTC by the main session)

The API-key bucket hit its monthly spend limit at roughly 01:35 UTC and cut
both fan-outs mid-flight (resets 08:10 SAST). What followed, in order:

- The four surviving fleet lanes were independently re-verified by the main
  session (their own verify legs had died on the wall) and shipped: features
  22/22, labels 11/11, WARN watcher 26/26, this build log.
- Dogfood commitment #1 — the session's own prediction about the W1 ideation
  run — was settled INCONGRUENT on the workflow's terminal evidence
  (precog-1788060140-8d7b475d): synthesis never ran, 297 of 310 agents died on
  spend. The chain's first refuted commitment is the author's own.
- The full W1 corpus was recovered from the journal: 74 distinct predictor
  candidates. The fleet's fail-closed gate had recorded 67 of them as
  "refuted" when zero refutations ran — a null checker leg absorbed into a
  DENY. They are UNJUDGED; the adversarial pass re-runs at the spend reset.
- The thesis trio was rebuilt main-thread (lib/continuity_trio.py): CF-IOF
  with the sigd margin/tau lift, Bernoulli NB, seeded SGD logistic
  regression. One measured defect found and fixed in the process: median
  binarisation put the discretisation threshold inside the majority class
  (0.625 train accuracy on separable data); supervised class-midpoint
  thresholds repaired it to green (6/6).
- The CF-IOF lift itself was then mechanically gated: the sigd-to-verdict
  mapping ran through lib/gentner_enforcer with the lattice geometry and
  generation task declared orthogonal. Verdict: Structural mapping validated: 100/100.
- CONGRUENCE.md's predictor chapter was synthesised from the corpus under an
  explicit honesty clause, PR #5170 opened with the working layers, and the
  capability stats regenerated when the watcher hook became the repo's 112th.

Three of the session's own failures are recorded above as live specimens;
the night added a fourth class: a fail-closed gate under infrastructure
failure manufactures false negatives — dead checkers must be marked
UNCHECKED, never absorbed into verdicts.
