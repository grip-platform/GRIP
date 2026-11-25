/**
 * converge-fleet — copy-adaptable dynamic Workflow template.
 *
 * SUPERSEDED by templates/converge-fleet-workflow-v2.js. Copy THAT file for a new
 * run. This one is kept because the four load-bearing mechanisms documented below
 * are still correct and v2 inherits them — not because it is still the default.
 *
 * What v2 changes, and why it is not cosmetic: the Verify phase below fans N
 * Anthropic agents out in a terminal parallel() barrier, so every verifier shares
 * one quota and they fail TOGETHER. Run wf_bef8da20-5e1 lost 26 of its 33 agents
 * to the Anthropic monthly spend limit exactly that way — every discovery lane
 * succeeded and the entire verification phase died, leaving 318 claims and zero
 * evidence. That run is what the operator ratified v2 on, 2026-08-31. A separate
 * hand-written script reproduced the same shape on 2026-09-17: 24 of 26 verifiers
 * died together, 72 findings, zero confirmed, 9.48M tokens, $303.96.
 *
 * v2 runs verification as ONE Pool-A agent driving lib/fleet_verify.py over Pool B,
 * so a limit can cost you the driver but never the whole phase.
 *
 * Shape: Scout -> parallel expert lanes -> cross-learning converge rounds -> adversarial verify.
 * Adapt the CONFIG block, the two schemas, the COMMON brief, and the lane prompts.
 * Everything else is the mechanism and should survive the copy unchanged.
 *
 * Four things are load-bearing. Dropping any of them silently degrades the run into an
 * ordinary parallel fan-out that looks identical in the transcript:
 *
 *   1. stallMs on EVERY read-heavy lane. Claude Code's workflow runner kills an agent
 *      after 180s with no progress events and retries up to 6 times. The timer is cleared
 *      during tool calls and re-armed after the last tool result, so the fatal window is
 *      the model's own long generation AFTER its reads — exactly what a read-then-
 *      synthesise lane does. One workflow burned 5.4M tokens on 12 dead attempts before
 *      this was found. GRIP#5136. Never set stallMs: 0 — that disables real-hang
 *      protection entirely.
 *   2. The shared `learned` set. Each lane returns what it discovered; the union of ALL
 *      lanes flows into the NEXT round's prompt. That union is the whole point: it is how
 *      lane A's discovery reaches lane B's successor. Keep the learnings per-lane and you
 *      have parallelism without cross-learning. Note the scope precisely: a round carries
 *      the pooled learnings of the round BEFORE it, not a cumulative union of every round
 *      (the set is reset each round; `seen` is what accumulates).
 *   3. The `searched` field on every schema. A negative result is only usable if its scope
 *      is named. "Found nothing" is worthless; "found nothing in these 204 PDFs, of which
 *      3 had no text layer" is evidence.
 *   4. A failed agent is a NAMED GAP, never a silent zero. `parallel()` resolves a failed
 *      leg to null; treat null as "this scope was NOT covered" and say so in the result,
 *      or the run reports coverage it never had. This applies to the VERIFY phase too, and
 *      that is the easy one to miss: filtering out failed checker legs makes the finding
 *      they were checking disappear from the report entirely, so the run looks cleaner
 *      the more of its checkers died. Re-join by index and mark it UNCHECKED instead.
 *
 * RESUME DISCIPLINE — why the lane prompts below contain no clock, no counter, no randomness.
 *
 * A workflow killed mid-flight (usage limit, crash, deliberate stop) resumes with
 * `Workflow({ scriptPath: '<same path>', resumeFromRunId: 'wf_...' })`. The runner's own
 * schema states the contract: "Completed agent() calls with unchanged (prompt, opts) return
 * their cached results instantly; only edited or new calls re-run. Same-session only. Stop
 * the prior run first before resuming." (verbatim from the Claude Code 2.1.251 tool schema).
 *
 * The cache key is the PAIR (prompt, opts). So any `Date.now()`, timestamp, elapsed count,
 * shuffled list or random sample interpolated into a lane prompt changes the key on resume
 * and forfeits a completed result you already paid for. Keep lane prompts a pure function of
 * the CONFIG block.
 *
 * Be honest about the reach of that, because it is not uniform. The lane and scout prompts
 * here are static, so they cache. The CONVERGE-round prompts deliberately interpolate live
 * state — the running total, the pooled vocabulary, the known-keys list — so they legitimately
 * re-run on resume. That is the right trade and it lands well: the expensive read-heavy lanes
 * are exactly the ones that cache, and a re-run round simply re-derives from state the script
 * rebuilds anyway. A lane that FAILED never counted as completed, so it re-runs on resume —
 * which is precisely what its named gap in `searched` is telling you to go and re-run.
 *
 * Keep the script at a stable path. Resume is keyed on `scriptPath`, and the runner takes
 * `scriptPath` over an inline `script`, so an edit-and-re-invoke cycle also wants a real file.
 *
 * The example domain is deliberately generic: find every reachable call site of a
 * deprecated API in an unfamiliar monorepo. Swap it for yours.
 */

export const meta = {
  name: 'acme-widgets-deprecation-sweep',
  description: 'Converge search (depth<=11) for every reachable call site of the deprecated acme-widgets v1 renderWidget API',
  phases: [
    { title: 'Scout', detail: 'inventory the space, seed the vocabulary' },
    { title: 'Lanes', detail: 'parallel expert lanes over disjoint slices' },
    { title: 'Converge', detail: 'cross-learning rounds until dry' },
    { title: 'Verify', detail: 'adversarial per-finding check' },
  ],
}

// Hard ceiling on this script's RUN TOTAL agent dispatch, enforced at every
// spawn() call below -- never a per-phase slice -- by
// hooks/workflow-budget-gate.py (rules/fan-out-spends-the-weekly-limit.md).
// Worst case, counting every dispatch SITE times its maximum repetitions:
// 1 scout + 4 lanes + up to 6 converge rounds (DEPTH_BUDGET=11 minus the 5
// already spent by scout+lanes, one agent() per round) + up to VERIFY_CAP=24
// verify checks = 35. (AGORA review, GRIP#8710: the PREVIOUS cap of 24
// matched only the Verify phase and ignored scout+lanes+converge entirely.)
const GRIP_MAX_AGENTS = 35

// Counts every agent this run actually spawns and throws once the RUN TOTAL
// above is reached, so the cap bounds the whole run -- not one phase's
// fan-out alone -- regardless of which phase is dispatching when the
// ceiling is hit (GRIP#8710 finding 2). Every agent() call below is routed
// through this wrapper.
let spawnedAgents = 0
async function spawn(prompt, opts) {
  if (spawnedAgents >= GRIP_MAX_AGENTS) throw new Error(`GRIP_MAX_AGENTS ${GRIP_MAX_AGENTS} reached`)
  spawnedAgents += 1
  return agent(prompt, opts)
}

// ---------------------------------------------------------------------------
// CONFIG — the only block most adaptations need to touch.
// ---------------------------------------------------------------------------
const DEPTH_BUDGET = 11      // total rounds INCLUDING scout + the lane phase
const DRY_EXIT = 2           // consecutive rounds yielding nothing new that end the loop
const VERIFY_CAP = 24        // hard ceiling on the adversarial fan-out
const TERMS_PER_ROUND = 12   // learned terms carried into each round's prompt. The pool is
                             // unioned across every lane and then CAPPED here, so learning
                             // compounds while the prompt stays bounded — without the cap a
                             // productive early round can crowd out the task itself.
                             // SET TO 0 TO RUN THE ABLATION: rounds still run, but carry no
                             // cross-lane vocabulary. Comparing the findings of a normal run
                             // against an ablated one is the A/B that can falsify this whole
                             // skill's central claim — see SKILL.md "Falsification".
const KNOWN_KEYS_IN_PROMPT = 60  // how many already-seen keys to serialise into a round prompt,
                             // so the round returns only genuinely NEW findings. This is what
                             // makes dry-exit honest: without it a round happily re-reports
                             // hits the lanes already found, the fresh count never reaches
                             // zero, and the loop runs to its budget looking productive.
const STALL_MS = 600000      // 10 min per-agent stall budget. NEVER 0. (GRIP#5136)
const REPO = '/absolute/path/to/the/monorepo'      // REQUIRED EDIT — no sensible default
const SCRATCH = '/absolute/path/to/scratch/acme-sweep'  // REQUIRED EDIT — no sensible default

// ---------------------------------------------------------------------------
// SCHEMAS — `learned` is the cross-learning channel; `searched` is the scope statement.
// ---------------------------------------------------------------------------
const FINDINGS = {
  type: 'object',
  required: ['findings', 'searched'],
  properties: {
    findings: {
      type: 'array',
      items: {
        type: 'object',
        required: ['locator', 'quote', 'bearing'],
        properties: {
          locator: { type: 'string', description: 'file:line — enough to re-find it exactly' },
          quote: { type: 'string', description: 'the matching line verbatim, max 300 chars' },
          bearing: { type: 'string', description: 'reachable-call | dead-code | re-export | dynamic-dispatch | test-only | weak' },
        },
      },
    },
    learned: {
      type: 'array',
      items: { type: 'string' },
      description: 'CROSS-LEARNING CHANNEL: new symbol names, aliases, re-export paths, string keys or spellings YOU discovered that another lane should sweep for next round. An empty array is a valid and honest answer — do not invent terms to look productive.',
    },
    searched: {
      type: 'string',
      description: 'the EXACT scope actually covered: roots, file counts by type, the method used, and anything that could NOT be searched (binary, generated, too large, unreadable). A negative result is only as good as this statement.',
    },
  },
}

const VERDICT = {
  type: 'object',
  required: ['verdict', 'reasoning'],
  properties: {
    verdict: { type: 'string', description: 'CONFIRMED | WEAK | REFUTED' },
    reasoning: { type: 'string', description: 'why, 1-2 sentences, from the context you re-read yourself' },
    evidence: { type: 'string', description: 'the surrounding lines that settle it, verbatim, max 400 chars' },
  },
}

// ---------------------------------------------------------------------------
// COMMON — the brief every lane shares. Constraints, scope, hard rules, output contract.
// ---------------------------------------------------------------------------
const COMMON = `You are one lane of a convergent multi-agent sweep over the acme-widgets monorepo at ${REPO}.
THE QUESTION: where is the deprecated v1 API \`renderWidget\` still REACHABLE from production code?
HARD RULES:
- LOCAL ONLY: no WebSearch, no WebFetch, no network. Read + Grep + Bash only.
- Never read dependency or build directories (node_modules, dist, build, .next, vendor, target).
- ripgrep: use \`rg -n\`, NEVER \`rg -r\` (that is --replace and silently rewrites your output).
  Gitignored content needs \`rg --no-ignore-vcs\`.
- macOS ships no GNU \`timeout\`; bound long children with python3 subprocess.run(timeout=N).
- Scratch space: ${SCRATCH} (mkdir -p your own subdirectory).
- Report only what you can point at. Quote verbatim, keep quotes short, and never paraphrase a
  quote into something the file does not say.
- Fill "searched" honestly and exactly. Fill "learned" only with terms you genuinely found.
Return via StructuredOutput.`

// ---------------------------------------------------------------------------
// State + merge helpers. Kept as named functions so the converge loop below contains
// no loop of its own — dedup is a Set membership test, never a scan.
// ---------------------------------------------------------------------------
const all = []
const scope = []
const seen = new Set()
let learned = new Set()

const keyOf = (f) => `${f.locator}|${(f.quote || '').slice(0, 40)}`

/** Absorb one agent result: findings, learnings, and its scope statement. Null = named gap. */
const absorb = (r, label) => {
  if (!r) { scope.push(`${label}: AGENT FAILED — scope NOT covered`); return 0 }
  scope.push(`${label}: ${r.searched || 'NO SCOPE STATEMENT — treat this lane as uncovered'}`)
  learned = new Set([...learned, ...(r.learned || [])])
  const fresh = (r.findings || []).filter(f => !seen.has(keyOf(f)))
  fresh.map(keyOf).map(k => seen.add(k))
  all.push(...fresh.map(f => ({ ...f, lane: label })))
  return fresh.length
}

// ---------------------------------------------------------------------------
// PHASE 1 — SCOUT MATERIALISES THE SLICES.
//
// The scout does not just look around: it WRITES one working file per lane into scratch and
// reports each file's line count. Three things follow from that, and all three are the reason
// this phase exists at all:
//
//   - Cheaper. Each lane reads a prepared file instead of re-walking the corpus, so the
//     expensive sweep happens once rather than once per lane.
//   - Reproducible. Every lane sees a fixed input, so a re-run or a resume grades the same
//     material, and a disagreement between lanes is about judgement rather than about which
//     files each happened to reach.
//   - Auditable. The line counts ARE the scoped-negative evidence. "Lane 2 found nothing"
//     means something only once you can say it read all 1,284 lines of tests.txt.
//
// Slice names below must match the lane prompts. Keep them boring and stable — a lane prompt
// that names a file is still a deterministic prompt, and so still caches on resume.
// ---------------------------------------------------------------------------
phase('Scout')
const scout = await spawn(`${COMMON}
TASK: build the working set in ${SCRATCH}/ (mkdir -p). Write one file per downstream lane so
each lane reads a prepared slice instead of re-searching the corpus.
(1) inventory.txt — every source file under ${REPO} excluding dependency and build dirs,
    grouped by extension with per-extension counts.
(2) seed.txt — application-source lines matching \`renderWidget\`, 2 lines of context, deduped.
(3) tests.txt — the same over test trees and fixture directories only.
(4) config.txt — every JSON/YAML/TOML value, feature flag, plugin-registry entry or dispatch
    key whose string could resolve to the v1 renderer, plus every re-export or barrel file
    that could forward the symbol under another name.
(5) aftermath.txt — everything written AFTER the v2 cutover that talks about the migration:
    commit subjects and bodies, PR descriptions, issue and review comments, changelog entries,
    and code comments added post-cutover. Use the version-control history, not just the tree.
    If this corpus keeps no such record — no commit log, a squashed history, no ticket export
    — write the file empty and SAY SO in "searched" rather than padding it; lane 4 is then
    reporting on an absent record, which is a different statement from finding nothing.
REPORT the line count of each of the five files explicitly, and put those counts in "searched"
— downstream lanes inherit them as the scope their negatives rest on. Return findings=[] (this
is the inventory step) and put every alias, wrapper name, barrel path or string key you noticed
into learned — those seed round 1.`,
  { label: 'scout', phase: 'Scout', schema: FINDINGS, effort: 'low', stallMs: STALL_MS })

absorb(scout, 'scout')

// ---------------------------------------------------------------------------
// PHASE 2 — LANES. Disjoint slices in parallel. Each READS its prepared file (pattern 1),
// and each returns findings AND learnings.
// ---------------------------------------------------------------------------
phase('Lanes')
const LANES = ['source', 'tests', 'config', 'retrospective']
const laneResults = await parallel([
  () => spawn(`${COMMON}
LANE 1 — APPLICATION SOURCE. READ ${SCRATCH}/seed.txt IN FULL, then follow every hit into the
file it came from. For each: is the call site reachable from an entry point, or is it dead
behind a removed flag? Classify with bearing. learned: any wrapper, adapter or alias you had to
follow. searched: the line count of seed.txt and how many hits you resolved.`,
    { label: 'source', phase: 'Lanes', schema: FINDINGS, effort: 'high', stallMs: STALL_MS }),

  () => spawn(`${COMMON}
LANE 2 — TESTS AND FIXTURES. READ ${SCRATCH}/tests.txt IN FULL. A test-only call is bearing
"test-only" and does NOT block the deprecation — but a fixture a production loader also reads
DOES. Distinguish them and say which. learned: fixture key names and helper wrappers.
searched: the line count of tests.txt and what you deliberately discarded as noise.`,
    { label: 'tests', phase: 'Lanes', schema: FINDINGS, effort: 'high', stallMs: STALL_MS }),

  () => spawn(`${COMMON}
LANE 3 — CONFIG AND DYNAMIC DISPATCH. The hardest lane: call sites that never name the symbol.
READ ${SCRATCH}/config.txt IN FULL and judge which entries resolve to the v1 renderer at
runtime. learned: every string key you find that a dispatcher could map to it — these are
exactly what the other lanes cannot see, and they are the highest-value thing you can hand
forward. searched: the line count of config.txt.`,
    { label: 'config', phase: 'Lanes', schema: FINDINGS, effort: 'high', stallMs: STALL_MS }),

  () => spawn(`${COMMON}
LANE 4 — RETROSPECTIVE. READ ${SCRATCH}/aftermath.txt IN FULL. You are not sweeping the code:
you are reading what the team SAID ABOUT the migration AFTER it happened — commit messages, PR
descriptions, issue threads, review comments, changelog entries, post-cutover code comments.
Hunt their own after-the-fact characterisations: claims that a module was fully migrated, an
admission that something was left behind, a "we'll come back to the config path", a rename
nobody documented. These retrospective statements are often the single highest-value evidence
about what was really done and what was only believed to be done, because they were written by
people who knew and had no reason to perform. Report a finding only where a statement points at
a concrete call site; put the naming history and every abandoned or interim name into learned.
searched: state THREE things separately — (a) the line count of aftermath.txt, (b) the earliest
and latest dates it covers, and (c) any record you expected to find and could not (a squashed
history, a missing issue export, an archive you had no access to).`,
    { label: 'retrospective', phase: 'Lanes', schema: FINDINGS, effort: 'high', stallMs: STALL_MS }),
])

laneResults.map((r, i) => absorb(r, LANES[i]))
log(`lanes: ${all.length} findings, ${learned.size} learned terms, ${scope.length} scope statements`)

// ---------------------------------------------------------------------------
// PHASE 3 — CONVERGE. Each round receives the pooled learnings of the round before it:
// every lane's `learned` merged into one set. The round barrier is what makes agents
// that never talk to each other nonetheless learn from each other.
//
// The set is RESET each round, so a round carries the PREVIOUS round's pool, not a
// cumulative union of every round. That is deliberate: a term already swept yields
// nothing on a second sweep, so re-sending it spends prompt budget for no information.
// What IS cumulative is `seen` — the dedup key set — so a finding can never be counted
// twice even though the vocabulary rolls forward one round at a time.
// ---------------------------------------------------------------------------
phase('Converge')

// THE CRITERION — the one thing every adaptation MUST write for itself. The example
// below is a worked illustration, not a default worth keeping.
//
// Two properties make it safe, and both are easy to lose:
//   - it is never VACUOUSLY true. `[].every(...)` returns true, so a criterion that only
//     says "every dynamic-dispatch finding has a locator" is satisfied by finding none
//     at all. Require the class to be non-empty before asserting a property of it.
//   - it is evaluated only AFTER a converge round, never before the loop (see below).
// Dryness alone is a weak criterion: it says "we found no more", not "we found enough".
const criterionMet = () => {
  const dispatch = all.filter(f => f.bearing === 'dynamic-dispatch')
  return all.some(f => f.bearing === 'reachable-call') &&
    dispatch.length > 0 && dispatch.every(f => f.locator)
}

/** One converge round. Returns the count of genuinely new findings. */
const convergeRound = async (n) => {
  const terms = Array.from(learned).slice(0, TERMS_PER_ROUND)
  learned = new Set()   // reset: a round carries only what the PREVIOUS round discovered
  const r = await spawn(`${COMMON}
CONVERGE ROUND ${n}/${DEPTH_BUDGET}. Rounds so far found ${all.length} call sites. The vocabulary
the PREVIOUS round surfaced — pooled from EVERY lane, so most of this was found by a lane other
than the one that will now use it: ${JSON.stringify(terms)}.
TASK: sweep the whole repo for these terms (rg -n, 2 lines of context), read every hit, and return
ONLY findings NOT already known. Known locators: ${JSON.stringify(Array.from(seen).slice(0, KNOWN_KEYS_IN_PROMPT))}.
Propose further learned terms ONLY if genuinely new. If these sweeps produce nothing new, return an
empty findings array and say so — honest dryness ends the loop and is the right answer.`,
    { label: `converge-r${n}`, phase: 'Converge', schema: FINDINGS, effort: 'high', stallMs: STALL_MS })
  return absorb(r, `round-${n}`)
}

// DEPTH-BUDGET ACCOUNTING ACROSS PHASES. The budget measures TOTAL EFFORT, not loop
// iterations: the scout is round 1 and the parallel lanes are rounds 2..N+1, so the loop
// starts from there rather than from zero. Widening the lane phase therefore buys fewer
// converge rounds, which is the correct trade to have to make consciously — and it means
// "depth 11" costs about the same whether the work lands in lanes or in rounds. `depth` is
// returned as `rounds_used` beside `depth_budget` so a reader can see how it was spent.
let depth = 1 + LANES.length   // scout + lanes already consumed this much of the budget
log(`depth budget ${DEPTH_BUDGET}: scout + ${LANES.length} lanes consumed ${depth}, ${DEPTH_BUDGET - depth} rounds remain`)
let dry = 0
// Starts false, never `criterionMet()`. The criterion is a property of the CONVERGED
// state, and the lane phase has converged nothing yet — so evaluating it before the loop
// lets a single lucky lane finding skip every round and, with it, the entire mechanism
// this template exists to run. Evaluate only at the end of each round.
let satisfied = false

while (depth < DEPTH_BUDGET && dry < DRY_EXIT && learned.size > 0 && !satisfied) {
  depth += 1
  const fresh = await convergeRound(depth)
  dry = fresh ? 0 : dry + 1
  satisfied = criterionMet()
  log(`round ${depth}: +${fresh} fresh (total ${all.length}), learned=${learned.size}, dry=${dry}, criterion=${satisfied}`)
}

// ---------------------------------------------------------------------------
// PHASE 4 — VERIFY. Independent adversarial re-check, defaulting to the negative.
// A finding nobody tried to refute is a claim, not evidence.
// ---------------------------------------------------------------------------
phase('Verify')
const strong = all.filter(f => f.bearing !== 'weak')
const budgetCapped = strong.slice(0, VERIFY_CAP)
if (strong.length > VERIFY_CAP) log(`verifying ${VERIFY_CAP} of ${strong.length} strong findings (budget cap)`)
const toVerify = budgetCapped.slice(0, GRIP_MAX_AGENTS)
if (budgetCapped.length > GRIP_MAX_AGENTS) {
  log(`GRIP_MAX_AGENTS cap (${GRIP_MAX_AGENTS}): dropped ${budgetCapped.length - GRIP_MAX_AGENTS} more finding(s) past the budget cap`)
}

const checks = await parallel(toVerify.map(f => () =>
  spawn(`${COMMON}
ADVERSARIAL CHECK of ONE finding. FINDING: ${JSON.stringify(f)}.
TASK: locate this exact line yourself (rg its distinctive tokens), read 10 lines either side, and
judge honestly: is this call site genuinely REACHABLE from a production entry point, or is it dead
code, a comment, a test double, a same-named symbol from a different module, or a string that
merely looks like the API? Default REFUTED when uncertain. Do not defer to the lane that reported
it — you are here to disagree with it if the code disagrees with it.`,
    { label: `verify:${f.locator}`, phase: 'Verify', schema: VERDICT, effort: 'high', stallMs: STALL_MS })))

// Re-join by index rather than filtering. A checker that fails resolves to null, and
// dropping those legs would make the finding VANISH from the report with nothing saying
// the check never happened — the exact silent failure this template exists to prevent,
// reappearing one phase later. A failed checker is a named gap, like a failed lane.
//
// Both observed failure shapes land on UNCHECKED, which is why the falsy test is written
// this way rather than as an explicit `=== null`: a leg whose agent REJECTED arrives as
// null, and a leg whose agent RESOLVED to null arrives as null too (under an earlier
// `.then(v => ({...f, check: v}))` formulation that same case produced a record carrying
// `check: null`, which read as a completed check that had returned nothing).
//
// Index alignment is the load-bearing assumption: `parallel()` preserves positions and
// fills failures with null, so `checks[i]` belongs to `toVerify[i]`. Note the degradation
// direction if that ever stopped holding — a short array yields `undefined`, which is also
// falsy, so findings would be over-reported as UNCHECKED rather than silently deleted.
// Wrong in the safe direction, and visible in the `unchecked` count.
const UNCHECKED = { verdict: 'UNCHECKED', reasoning: 'the verifying agent failed; this finding was never checked' }
const verified = toVerify.map((f, i) => {
  if (!checks[i]) scope.push(`verify:${f.locator}: CHECKER FAILED — finding NOT verified`)
  return { ...f, check: checks[i] || UNCHECKED }
})

// ---------------------------------------------------------------------------
// RESULT — findings, the criterion outcome, and the scope every negative rests on.
// ---------------------------------------------------------------------------
return {
  findings: verified,
  unchecked: verified.filter(f => f.check === UNCHECKED).length,
  unverified_weak: all.filter(f => f.bearing === 'weak').length,
  uncapped_strong: strong.length,
  rounds_used: depth,
  depth_budget: DEPTH_BUDGET,
  criterion_met: satisfied,
  exit_reason: satisfied ? 'criterion' : (dry >= DRY_EXIT ? 'dry' : (learned.size === 0 ? 'no-new-vocabulary' : 'depth-budget')),
  searched: scope,
}
