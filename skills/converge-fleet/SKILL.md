---
name: converge-fleet
description: Runs a /converge loop whose parallel expert lanes cross-learn — every lane's discoveries are pooled and fed into the next round, so agents that never talk to each other still learn from each other. Ships a copy-adaptable dynamic Workflow template (scout, parallel lanes, cross-learning rounds to a criterion or dry-exit, adversarial verify) with stall-timeout, scoped-negative and budget discipline baked in. Use when one question needs breadth and depth at once over a large corpus or codebase, where the search vocabulary only emerges as the sweep proceeds. Thin orchestrator - composes the Workflow tool, /broly and the ultracode kernel as code and takes /converge's loop semantics; only the cross-learning barrier and the scoped-negative handling are new.
status: Active
version: 1.0.0
triggers:
  - /converge-fleet
  - "converge fleet"
  - "parallel lanes that learn from each other"
see_also:
  - converge
  - ultracode
  - ultrawin
  - broly-meta-agent
  - loop-engineering
---

# /converge-fleet — a converge loop whose lanes learn from each other

**Plain English**: you have one question and a corpus too big to read. A single agent
runs out of context; a flat parallel fan-out finishes fast but each lane only knows what
it was told at the start. `/converge-fleet` runs both: several expert lanes sweep
disjoint slices in parallel, each reports not only what it *found* but what it *learned*
(a new alias, a nickname, a re-export path, a spelling nobody anticipated), and the union
of all those learnings is handed to the next round. A term discovered by the lane reading
config reaches the lane reading source code on the very next pass. The loop runs to a
stated criterion, a depth budget, or honest dryness, then an independent adversarial pass
tries to refute every finding before any of it is reported.

V>>'s framing, which is the whole design: *"every /broly agent can learn from each other.
every LLM can learn from each other."*

`/converge-fleet` is a **thin orchestrator**. It sequences existing kernels and adds two
mechanisms of its own: the shared learned-state barrier, and the scoped-negative handling
that makes an absence of findings auditable. If a third step seems to need new logic, find
the kernel.

**Be precise about what "thin" means here, because one line is genuinely an exception.**
Everything below is imported as *code* except the loop itself. A dynamic Workflow script
is JavaScript evaluated by the harness's workflow runner; `lib/converge.py` is Python and
is not reachable from it. So the template implements the converge *shape* — criterion,
depth budget, dry-exit, best-so-far — in the substrate where the fan-out has to live, and
imports `/converge`'s **semantics** rather than its **implementation**. That is a
restatement, and calling it anything else would be false. The two additions that are
genuinely new are the cross-learning barrier and the scoped-negative handling; everything
else is delegated.

## What it does NOT do (composes, never duplicates)

| Concern | Imported kernel (do NOT restate its logic here) |
|---|---|
| Loop **semantics** — criterion, depth budget, velocity, dry-exit | `skills/converge/SKILL.md` + `lib/converge.py` (`rules/loop-engineering.md`) — semantics only; the JS template restates the shape because it cannot call the Python kernel |
| Orchestration substrate | the native Claude Code **Workflow tool** (dynamic workflows) — `phase()`, `agent()`, `parallel()`, `log()` |
| Idempotent replay, provider/harness agnosticism | `lib/ultracode` — `skills/ultracode/SKILL.md` |
| Multi-model diversity, per-role LLM assignment, criterion-mesh | `skills/broly-meta-agent/SKILL.md` + `lib/broly_council.py` |
| Whether to fan out at all | `rules/dispatch-economics.md` (fan out only when useful work is ≥ 3× the agent floor) |
| Cost routing for heavy cognition | `rules/pool-a-offload-routing.md` — structure on the Workflow tool, councils through HAL Pool B |
| Claim discipline on every negative | `rules/prove-it-claims-canonical.md` + `rules/verify-canonical-not-claimed.md` |
| Capstone delivery loop (implement → AGORA → merge) | `skills/ultrawin/SKILL.md` — a *sibling*, not a parent |

## The mechanism (four parts — dropping any one silently degrades the run)

**1. Cross-lane learning.** Every lane's output schema carries a `learned` array beside
its findings. After each phase the workflow unions them into one set and embeds that
union in the next round's prompt. Be precise about what this is: lanes do **not** message
each other mid-flight, and no lane sees another's partial work. The sharing happens at
the round barrier — cheap, deterministic, and enough, because the thing worth sharing is
vocabulary, and vocabulary is small. Round *n+1* is strictly better informed than round
*n* was; that is the compounding.

**2. Stall discipline — `stallMs: 600000` on every read-heavy lane.** Claude Code's
workflow runner kills an agent after **180 seconds with no progress events** and retries
up to six times. The timer is cleared while tool calls are in flight and re-armed after
the last tool result, so the fatal window is the model's own long generation *after* its
reads — precisely what a read-then-synthesise lane does. Bash-heavy lanes never trip it;
synthesis lanes die mid-sentence and the runner reports a retry, not a defect. One
workflow burned **5.4M tokens on 12 dead attempts** before this was diagnosed
(GRIP#5136). Set `stallMs` on every lane that reads then thinks. **Never set it to 0** —
that disables real-hang protection. For a synthesis too large for ten minutes, split it
into pipeline stages rather than raising the number further; heartbeat-echo mitigations
do not work, because the kill lands inside one continuous generation where no tool call
can be interleaved.

**The guard does not help a lane whose SINGLE compile step exceeds it.** Measured 2026-09-10 on
the WhatsApp forensic fleet: seven verify agents died at exactly the 600s kill while compiling
large structured outputs — `stallMs` was honoured; the one generation was simply longer than ten
minutes. Raising `stallMs` only moves the cliff. The fix is to bound what one lane must emit:
**cap findings per lane** (and per verify leg) so no single structured-output compile can run past
the budget, then resume from cache. A lane that must return hundreds of findings is two lanes.

**3. Scoped negatives.** Every schema carries a `searched` string, and the workflow
returns the collected scope statements alongside the findings. "Found nothing" is not a
result; "found nothing across these roots, these file counts, by this method, and these
three files could not be read" is. A lane that returns no scope statement is treated as
**uncovered**, and a `parallel()` leg that resolves to `null` is recorded as a **named
gap** — never a silent zero. Absence of a finding is only evidence when the search that
produced it is named.

**4. Adversarial verify.** The last phase re-checks each finding in a fresh agent that
re-reads the source itself, defaults to the negative when uncertain, and is told
explicitly to disagree with the lane that reported it. A finding nobody tried to refute is
a claim, not evidence. The named-gap rule from part 3 applies here too, and this is the
easy place to lose it: filtering out failed checker legs deletes the finding they were
checking, so a run looks *cleaner* the more of its checkers died. Re-join the results by
finding id and mark the survivors `UNCHECKED` instead, with the count in the output.

## The loop

```text
Scout      1 cheap agent   narrows the space, seeds the vocabulary
Lanes      N in parallel   disjoint slices; each returns findings + learned + searched
Converge   rounds 1..k     each round gets the pooled learnings of the round before it
Verify     capped fan-out  independent refutation attempt per finding
```

A round carries the previous round's pool, **not** a cumulative union of every round. That
is deliberate: a term already swept returns nothing on a second sweep, so re-sending it
spends prompt budget for no information. What accumulates instead is the dedup key set, so
a finding is never counted twice even though the vocabulary rolls forward one round at a
time.

The criterion is evaluated only **after** a converge round, never before the loop. A
criterion checked up front can be satisfied by one lucky lane finding, which skips every
round and with it the entire mechanism — the template would then be an ordinary parallel
fan-out that merely looks like this one.

Independent exits, reported as `exit_reason` so a reader can tell success from
exhaustion:

| Exit | Meaning |
|---|---|
| `criterion` | the stated criterion held over the **unverified** findings -- the search finished. Whether the run **succeeded** is decided after Verify (see below) |
| `dry` | two consecutive rounds produced nothing new — honest dryness |
| `no-new-vocabulary` | v1: no lane learned anything to carry forward |
| `no-open-questions` | v2: no lane raised a question for another lane to answer |
| `token-ceiling` | v2: this run's output-token spend reached `TOKEN_CEILING - VERIFY_RESERVE`, or a "+N" target had `VERIFY_RESERVE` or less left. Verify and Synthesis still run on the reserve, but the answer is **incomplete**, say so |
| `depth-budget` | the budget ran out first — the answer is **incomplete**, say so |

Exactly one `exit_reason` is reported. When several hold at once, the template decides in
this order: `criterion`, `token-ceiling`, `dry`, `no-open-questions` (`no-new-vocabulary`
in v1), `depth-budget`.

State a real criterion. Dryness alone says "we found no more", never "we found enough";
a loop with only a dryness exit can only ever exhaust itself.

**Success is decided after Verify, never by the loop** (GRIP#6975). The v2 template joins
the checker's verdicts back onto finding ids and returns three answers, kept apart:
`search_finished` (the criterion held over unverified findings), `answer_established`
(the same criterion holds over the CONFIRMED findings alone) and `change_authorised`
(`SHIP` is on and at least one finding is CONFIRMED). `success` is `answer_established`.
Until 2026-10-02 the loop's verdict was returned as `criterion_met` and read as success,
so a run whose every check failed still called itself a success.

## Template

> **Copy `templates/converge-fleet-workflow-v2.js`, not the v1 file this section
> describes.** v1's Verify phase fans N Anthropic agents into a terminal `parallel()`
> barrier, so they share one quota and fail together — that is how run
> `wf_bef8da20-5e1` lost 26 of its 33 agents and reported 318 claims with zero
> evidence. See "v2 defaults" below. The three required edits described below apply to
> v2 unchanged — it carries the same `CONFIG` block with the same `REPO` and `SCRATCH`
> paths — and v2 additionally marks its question and lane prompts inline with
> `<<<EDIT ME>>>`.

`templates/converge-fleet-workflow.js` (v1) is a complete, generic, copy-adaptable dynamic
Workflow script implementing all four parts. Its worked example is a deprecated-API sweep
over a fictional `acme-widgets` monorepo, chosen because it has the same shape as the real
runs: many disjoint slices, a vocabulary (aliases, re-exports, string keys) that only
emerges mid-sweep, and findings worth refuting.

**Exactly three things must be edited before a copy will run**, and the template will fail
loudly rather than silently if they are not:

1. `REPO` and `SCRATCH` — the two absolute paths in CONFIG. They ship as
   `/absolute/path/to/...`, which no filesystem resolves, so an unedited copy dies on its
   first command rather than searching the wrong tree.
2. `criterionMet()` — the stopping rule. The shipped one is a worked illustration of a
   *safe shape*, never a default to keep.
3. The `COMMON` brief and the lane prompts — the question itself and how the work divides.

The two schemas and the remaining CONFIG numbers have sensible defaults and are optional.

```javascript
const DEPTH_BUDGET = 11      // total rounds INCLUDING scout + the lane phase
const DRY_EXIT = 2           // consecutive empty rounds that end the loop
const VERIFY_CAP = 24        // hard ceiling on the adversarial fan-out
const TERMS_PER_ROUND = 12   // learned terms per round; 0 runs the ablation (below)
const STALL_MS = 600000      // 10 min per-agent stall budget. NEVER 0. (GRIP#5136)
```

Two ways to write a criterion that cannot work, both easy to miss on review:

- **Vacuously true.** `[].every(...)` is `true`, so "every dynamic-dispatch finding has a
  locator" is satisfied by finding none at all. Assert the class is non-empty first.
- **Satisfiable by the lane phase alone.** Then the loop never runs a single round, and the
  cross-learning mechanism — the reason to use this skill — never executes.

## Six patterns the template encodes

These come from live runs rather than from design. Each is small, and each was learned by
something going wrong or costing more than it should have.

**1. The scout materialises the slices.** The first phase does not merely look around: it
*writes* one working file per lane into scratch and reports each file's line count. Lanes then
read a prepared file instead of re-walking the corpus. That is cheaper (the expensive sweep
happens once, not once per lane), reproducible (every lane and every resume grades identical
material, so a disagreement between lanes is about judgement rather than about which files each
happened to reach), and auditable — **the line counts are the scoped-negative evidence.** "Lane
2 found nothing" means something only once you can say it read all 1,284 lines of `tests.txt`.

**2. Depth-budget accounting spans the phases.** The budget measures total effort, not loop
iterations. The scout is round 1 and the lanes are rounds 2 to N+1, so the converge loop starts
from there. Widening the lane phase buys fewer converge rounds — the correct trade to have to
make consciously, and it keeps "depth 11" costing about the same however the work is arranged.

**3. Known keys go into the round prompt.** Each round's prompt carries a capped serialisation
of the already-seen keys, so the round returns only genuinely new findings. **This is what makes
dry-exit honest.** Without it a round cheerfully re-reports what the lanes already found, the
fresh count never reaches zero, and the loop runs to its budget looking productive.

**4. One lane reads the aftermath.** A dedicated lane over what came *after* the event, hunting
the participants' own retrospective characterisations of it — commit messages, review comments,
issue threads, notes written once the dust settled. These are often the single highest-value
evidence about what actually happened, because they were written by people who knew and had no
reason to perform. In the shipped example that is the post-cutover migration record; adapt the
window to whatever your event is.

This lane needs an aftermath record to exist, and the shipped example reads version-control
history to get one. Corpora vary: a document archive or a log corpus has no commit log, and a
repository with a squashed or rewritten history has a much thinner one than it appears to. Point
the lane at whatever your corpus actually keeps — a ticket system, a mailing list, an audit
trail, dated file revisions — and if there is genuinely no after-the-fact record, drop the lane
rather than let it return an empty result that reads like an absence of evidence. Left in place
it degrades honestly (an empty slice yields a zero line count in `searched`), but a lane that
cannot succeed is still a lane's worth of budget spent for nothing.

**5. The pooled vocabulary is capped per round.** Learnings are unioned across every lane and
then trimmed to `TERMS_PER_ROUND` before injection, so learning compounds while the prompt stays
bounded. Without the cap one productive early round can crowd out the task itself. This setting
is also the ablation switch: set it to `0` and the rounds still run but carry no cross-lane
vocabulary, which is the A/B described under [Falsification](#falsification) below.

**6. Resume discipline: keep lane prompts deterministic.** A run killed mid-flight — a usage
limit, a crash, a deliberate stop — resumes via `Workflow({ scriptPath, resumeFromRunId })`. The
runner's own schema states the contract: *"Completed agent() calls with unchanged (prompt, opts)
return their cached results instantly; only edited or new calls re-run. Same-session only. Stop
the prior run first before resuming."* The cache key is the pair `(prompt, opts)`, so any clock
reading, counter or random sample interpolated into a lane prompt changes the key and forfeits a
completed result already paid for.

Be honest about how far that reaches. The scout and lane prompts are static and so they cache;
the converge-round prompts deliberately carry live state and so they legitimately re-run. That
lands well — the expensive read-heavy lanes are exactly the ones that cache, and a re-run round
re-derives from state the script rebuilds anyway. A lane that *failed* never counted as
completed, so it re-runs on resume, which is precisely what its named gap was telling you.

## Usage

```bash
/converge-fleet "<question>"              # depth budget defaults to 11
/converge-fleet 6: "<question>"           # /converge positional depth syntax
```

Requires the Workflow tool (ULTRACODE opt-in). Without it, degrade to `/converge` at
depth 0 and say that the fleet was unavailable — never silently run a weaker thing under
the stronger name.

## When NOT to use it

Per `rules/dispatch-economics.md`, the fleet has a real floor cost. Use plain `/converge`
or a single agent when the corpus fits one context window, when the search vocabulary is
fully known up front (a flat `parallel()` sweep is then strictly cheaper and just as
good), or when there are no disjoint slices to divide — three lanes over the same files
is three times the cost for one lane's information.

## Anti-patterns

| Anti-pattern | Why wrong |
|---|---|
| Omitting `stallMs` on a read-then-synthesise lane | The 180s watchdog kills it mid-generation and retries six times — GRIP#5136 |
| `stallMs: 0` to "be safe" | Disables real-hang protection; a genuinely hung agent then runs forever |
| Keeping each lane's learnings to itself | That is a flat fan-out wearing this skill's name — the union IS the mechanism |
| Reporting a negative without its `searched` scope | `prove-it-claims-canonical.md`: an unscoped negative is not a finding |
| Treating a `null` parallel leg as zero findings | Silently converts an uncovered scope into a clean bill |
| Filtering failed checker legs out of the verify results | Deletes the finding along with its check — the run then looks cleaner the more checkers died |
| A clock, counter or random sample inside a lane prompt | Changes the resume cache key, so a completed lane you already paid for re-runs from scratch |
| Lanes that re-search the corpus instead of reading the scout's slices | Pays for the same sweep once per lane, and the lanes no longer grade identical material |
| Dryness as the only exit condition | The loop can then only exhaust itself, never succeed |
| Running `VERIFY_ROUTE = 'local'` against a server with a QUANTISED KV cache | Verdicts stop reproducing — measured at 22 of 39 matching pairwise on `q8_0` against 38 of 39 on `f16`, with temperature already 0.0. The run looks fine and every `REFUTED` is a coin flip. `probe_determinism` detects it; `OLLAMA_KV_CACHE_TYPE=f16` fixes it |
| Uncapped verify fan-out | 200 findings becomes 200 agents; cap it and report what was left unverified |
| Evaluating the criterion before the first round | One lucky lane finding then skips every round and the whole mechanism |
| A criterion asserting a property of a possibly-empty set | `[].every(...)` is `true`; the loop exits having proved nothing |
| A criterion pairing findings ONE-TO-ONE across two bearings | One remedy routinely resolves many findings, so the ratio can never reach 1:1 and the run exhausts its budget looking unfinished. Measured in run `wf_1f710b7b-fc7`: 23 coupling findings against 7 break-strategies read as 30% complete, when the same data scored **17 of 17** once counted per distinct SUBJECT rather than per finding |
| A criterion counting a docstring mention or a lazy optional import as a hard dependency | Both look like coupling to a text search and neither constrains anything. In the same run, `lib.banach_projector` appeared only in a docstring and `lib.decision_record` is imported lazily and defaults to `None` — they were the last two "unresolved" items until the clean-room tree signed and verified without either |
| Writing a bespoke fleet script instead of adapting the shipped template | The v2 template's verify phase is ONE Pool-A agent driving Pool-B checks, precisely so a limit cannot kill the whole phase. A hand-written script that fans verification out across N Anthropic agents reintroduces the failure the template already solved — which is exactly what happened on 2026-09-17 (24 of 26 verifiers died together, 72 findings, zero confirmed, 9.48M tokens) |
| Launching a fan-out without running `grip-fanout-preflight` first | The estimate is the only thing standing between a large run and an unannounced spend. The 2026-09-17 run cost $303.96 in usage credits and announced nothing; the preflight refuses that exact shape unless the spend is explicitly accepted |
| Verification in a terminal `parallel()` barrier | Every verifier fails together when a limit is hit, so the run loses ALL its evidence rather than the tail of it. Pipeline verification per finding, or drive it off-pool as v2 does |
| Re-implementing converge semantics anywhere but the workflow script | The template restates the loop shape because a JS workflow cannot call the Python kernel — that is the one sanctioned restatement, not a licence for others |

## Falsification

The central claim — that pooling learnings between rounds finds things a flat parallel
sweep does not — is **directly testable, and the template ships the switch**. Set
`TERMS_PER_ROUND = 0` and the rounds still run but carry no cross-lane vocabulary. Run the
same question both ways and compare the findings that survive verification. If the two runs
agree, the barrier bought nothing on that question.

`/converge-fleet` is wrong if, over the next ~10 runs: that A/B shows no difference (the
barrier adds nothing — collapse it to a flat `parallel()` sweep and delete this skill); OR
the adversarial verify pass never refutes a lane's finding (it is ceremony — drop it); OR
runs routinely exit on `depth-budget` rather than `criterion` or `dry` (the budget is
mis-tuned, or the criteria being written are unsatisfiable — fix the criteria before raising
the budget); OR operators reach for a hand-written workflow script rather than this template
(the template does not fit real work — find out what it is missing). Track per run:
`{fresh findings per converge round, verify refutation rate, exit_reason, stall kills}`.
Success is findings that survived refutation, never lane count or round count.

## Lineage

Crystallised 2026-08-30 from four dynamic-workflow scripts run the same night: a
depth-11 converge search with vocabulary flowing forward and an adversarial verify phase,
a six-modality parallel sweep with scoped-negative discipline, and two four-lane
adversarial review panels. The stall-timeout discipline comes from the forensic diagnosis
of a workflow that lost 5.4M tokens to the 180-second watchdog (GRIP#5136).

Skill-versus-mode was put to a non-Anthropic sovereign council, which unanimously
returned *ship the invocable skill now; reserve a stateful mode until a recurring need
for persistent session posture is demonstrated*. Seal `38432509f76e68a5`, IDR leaf
`precog-1788050964-90e13d50`, judged by `gemini-3.1-pro-preview`; re-verify with
`grip-sovereign-council --verify 38432509f76e68a5`.

## v2 defaults (operator-ratified 2026-08-31)

`templates/converge-fleet-workflow-v2.js` supersedes the v1 template. Four changes,
each ratified by the operator after run `wf_bef8da20-5e1` lost 26 of its 33 agents to
the Anthropic monthly spend limit — with every discovery lane succeeding and the whole
verification phase dying, which left 318 claims and zero evidence.

**Ultrathink is unconditional.** Every agent prompt opens with an `/ultrathink`
directive and every reasoning phase runs at `effort: 'max'`. v1 left this to the caller
and the caller forgot.

**Verification runs on Pool B, and that is an epistemic fix before it is a cost one.**
A workflow `agent()` is always an Anthropic subagent — there is no pool switch on it —
so verify is now ONE Pool-A agent driving `lib/fleet_verify.py` over Pool B, instead of
N Pool-A agents doing the checking themselves. The cost argument is
`rules/pool-a-offload-routing.md`: the largest fan-out in a run should not spend the
weekly limit. The better argument is `rules/exogenous-anchor-law.md`: an Anthropic
agent checking an Anthropic agent's findings is a weak exogenous anchor, and a
non-Anthropic checker satisfies the vendor-identity test that a same-vendor one does
not. Set `VERIFY_ROUTE = 'local'` and every check runs on on-device ollama, so nothing
leaves the machine while qwen's Alibaba-lineage weights keep the verifier a different
vendor from the author — the route to use whenever the corpus is private.

**Every non-weak finding reaches the checker's selector** (GRIP#6976).
`lib/fleet_verify.prioritise` deals the `VERIFY_CAP` checks round-robin across bearings,
and runs them `VERIFY_WORKERS` at a time. The findings it leaves out come back
`UNCHECKED`, counted in the return's `verification.no_verdict`. Until 2026-10-02 the
template handed the selector only the first 40 findings in discovery order, so the rest
were never seen, and nothing counted them.

**Serve that model with an UNQUANTISED KV cache, or the verdicts do not reproduce.**
Measured 2026-09-17 (GRIP#6142) on 40 findings re-checked against an unchanged input:
with ollama's default-here `OLLAMA_KV_CACHE_TYPE=q8_0`, only **22 of 39 verdicts
matched pairwise** and 17 of 39 were unanimous across three runs. Re-serving the same
model with `OLLAMA_KV_CACHE_TYPE=f16` took pairwise agreement to **38 of 39**.

Temperature is already `0.0` on every call, so this is not the sampler. Greedy decoding
only repeats when the logits are bit-identical, 8-bit cache rounding varies with cache
state across sequential requests, and a verdict token is a near-tie between two words —
so the tie flips. A run served this way still produces verdicts; they are simply not
the same verdicts twice, which quietly makes every `REFUTED` a coin flip.

`lib/fleet_verify.py::probe_determinism` asks the same question twice and reports
whether the answers match. Its asymmetry is deliberate: a disagreement proves the
server cannot give reproducible verdicts, while agreement means "nothing detected"
rather than "reproducible", because one prompt sampled twice can miss instability that
shows across forty. Point the run elsewhere with `$GRIP_FLEET_VERIFY_OLLAMA_URL` if the
shared server cannot be reconfigured.

Be honest about what that pass can and cannot do. The checker has no repository access,
so it judges whether each quote actually supports its claim and whether the claim's
scope exceeds its quote. It cannot establish ground truth and will not catch "this code
has no caller". A `CONFIRMED` verdict there means internally coherent, not verified
against the tree, and the driver prompt is written to say so rather than let a reader
assume otherwise.

**Questions replace round counting as the loop driver.** Each lane now returns a
`questions` array beside `learned` — what it could not resolve from its own slice but
another lane probably can. The barrier routes each question to the lane that owns the
relevant slice, and only those lanes re-dispatch. A lane
with nothing asked of it costs nothing that round, and the loop exits when questions dry
up rather than when a counter runs out, which is a better stopping rule because it is a
property of the work rather than of the budget. Routing is keyword-based on purpose:
paying a model call to decide who answers a question would cost more than asking every
lane. A question matching no keywords still goes to a lane other than the one that
raised it, since being asked by someone else is the entire point of routing it. The
router tells lanes apart by a permanent `lane_id`, never by the round label an agent
carries (`r6:lane1`). Until GRIP#6978 it compared the two, so from the first converge
round on the asker was never excluded and a lane could be handed its own question.

**A spending ceiling keeps Verify's share** (GRIP#6977). `budget.spent()` counts output
tokens across the whole turn, so the template measures from its own start and starts no
converge round once that reaches `TOKEN_CEILING - VERIFY_RESERVE`, or once a "+N" target
has `VERIFY_RESERVE` or less left. Past a "+N" target the runtime makes `agent()` throw,
so without the reserve a loop could spend Verify's share and kill the one phase that
turns claims into evidence. A round already started is not interrupted, and the return's
`tokens_spent` is there to set the next run's ceiling. Each lane keeps at most
`FINDINGS_PER_LANE` findings across the run, the rest counted in `over_lane_cap`. Every
question no lane was ever asked -- dropped by `QUESTIONS_PER_ROUND`, unroutable, or still
open at the end -- is returned in `unanswered_questions`: the first `BACKLOG_CAP` verbatim,
and all of them counted.

**`SHIP = true` chains `/ultrawin`.** Off by default, and the default is the safety
property: read-only is what makes this template safe to point at a corpus nobody has
cleared for writing. The ship phase acts only on `CONFIRMED` findings and ships nothing
when none survive, which is a correct outcome rather than a failure. That is enforced in
code: the template does not start the ship phase at all unless Verify returned at least
one `CONFIRMED` verdict (GRIP#6975). Before that, the only gate was a sentence in the
ship agent's own prompt.

**One schema note worth copying.** v1's `bearing` field carried a long prose
description enumerating its allowed values, and several lanes wrote prose back into it
(`"egress-path — LIVE CODE, DORMANT PROCESS. The LlamaEngine imported at ..."`). A
field that wants one token must look like it wants one token.
`lib/fleet_verify.normalise_bearing()` repairs the damage, but the schema should not
cause it.

**Not yet wired: Pool C.** The routing story is Pool A for deep-read lanes and
synthesis, Pool B for verification. Pool C (Codex) is named in the design for bulk
mechanical slicing but no phase routes to it today — treat that as an open gap, not a
capability.
