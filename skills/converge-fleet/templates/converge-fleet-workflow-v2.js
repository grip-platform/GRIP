/**
 * converge-fleet v2 — copy-adaptable dynamic Workflow template.
 *
 * Shape: Scout -> parallel expert lanes -> question-routed converge -> Pool-B
 * adversarial verify -> synthesis -> optional /ultrawin ship.
 *
 * WHAT CHANGED FROM v1, and why each change exists. All four were ratified by the operator
 * on 2026-08-31 after run wf_bef8da20-5e1 lost 26 of 33 agents to the Anthropic spend limit.
 *
 * 1. ULTRATHINK IS THE DEFAULT, NOT AN OPTION. Every agent prompt opens with an /ultrathink
 *    directive and every reasoning phase runs at effort 'max'. v1 left this to the caller and
 *    the caller forgot; the lanes that did get it produced markedly better scope statements.
 *
 * 2. VERIFY MOVED TO POOL B, AND THAT IS AN EPISTEMIC FIX AS MUCH AS A COST ONE. A workflow
 *    agent() is ALWAYS an Anthropic subagent — there is no pool switch on it. So the verify
 *    phase is now ONE Pool-A agent that drives `lib/fleet_verify.py` over Pool B, instead of
 *    N Pool-A agents doing the checking themselves. Two consequences:
 *      - cost: the largest fan-out in the run stops spending the weekly Anthropic limit
 *        (`rules/pool-a-offload-routing.md`);
 *      - soundness: an Anthropic agent checking an Anthropic agent's findings is a weak
 *        exogenous anchor. A non-Anthropic checker satisfies the vendor-identity test in
 *        `rules/exogenous-anchor-law.md` that a same-vendor one does not.
 *    Where the corpus is private, `--route local` sends the checks to on-device ollama and
 *    nothing leaves the machine at all — still a different vendor, so still exogenous.
 *
 * 3. QUESTIONS REPLACE ROUND COUNTING AS THE LOOP DRIVER. v1 pooled `learned` vocabulary at
 *    the round barrier and ran a fixed number of rounds. v2 also pools `questions`: each lane
 *    states what it could not resolve from its own slice, the barrier routes each question to
 *    the lane that owns the slice which can answer it, and ONLY those lanes re-dispatch. A
 *    lane with no routed question costs nothing that round. The
 *    loop now exits when questions dry up rather than when a counter runs out, which is a
 *    better stopping rule because it is a property of the work rather than of the budget.
 *
 * 4. OPTIONAL /ultrawin SHIP PHASE. Set SHIP = true and the run hands its CONFIRMED findings
 *    to an implement-and-merge phase. Default false: the read-only default is what let this
 *    template sweep a private-data corpus safely, and it should stay the default.
 *
 * UNCHANGED FROM v1, because they are the mechanism: stallMs on every read-heavy lane
 * (GRIP#5136 — the stall-watchdog fix issue), the `searched` scope statement on every schema,
 * named gaps instead of silent zeros, and deterministic lane prompts so resume caches.
 */

export const meta = {
  name: 'converge-fleet-v2',
  description: 'Cross-learning multi-lane convergence with question routing, Pool-B verification and optional ship',
  phases: [
    { title: 'Scout', detail: 'materialise the lane slices, seed vocabulary' },
    { title: 'Lanes', detail: 'parallel expert lanes over disjoint slices' },
    { title: 'Converge', detail: 'question-routed re-dispatch until questions dry up' },
    { title: 'Verify', detail: 'Pool-B adversarial refutation, off the Anthropic limit' },
    { title: 'Synthesis', detail: 'reason over what survived refutation' },
    { title: 'Ship', detail: 'optional /ultrawin implement and merge' },
  ],
}

// Hard ceiling on this script's RUN TOTAL agent dispatch, enforced at every
// spawn() call below -- never a per-phase slice -- by
// hooks/workflow-budget-gate.py (rules/fan-out-spends-the-weekly-limit.md).
// Worst case, counting every dispatch SITE times its maximum repetitions:
// 1 scout + 4 lanes + up to 7 converge rounds (DEPTH_BUDGET=12 minus the 5
// already spent by scout+lanes) of up to 4 lanes each (28) + 1 verify-driver
// + 1 synthesis + 1 optional ship = 36. (AGORA review, GRIP#8710: the
// PREVIOUS cap of 4 matched only the Lanes phase's own fan-out and
// undercounted this script's real worst case by nine times.)
const GRIP_MAX_AGENTS = 36

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
const DEPTH_BUDGET = 12
const DRY_EXIT = 2
const VERIFY_CAP = 40         // how many findings Pool B CHECKS. Every non-weak finding is
                              // still written for the selector, lib/fleet_verify.prioritise,
                              // which deals this cap fairly across bearings; the ones it
                              // leaves out come back UNCHECKED and counted (GRIP#6976).
const VERIFY_WORKERS = 2      // checks in flight at once inside lib/fleet_verify.py. Kept
                              // small: on route 'local' one ollama server may answer them one
                              // at a time, and a check queued behind others can run into the
                              // module's 120 s per-check timeout and come back UNCHECKED.
                              // On 'poolb', 4 is reasonable.
const TERMS_PER_ROUND = 12
const QUESTIONS_PER_ROUND = 6 // routed questions carried into a round; the new loop driver
const KNOWN_KEYS_IN_PROMPT = 60
const STALL_MS = 600000       // NEVER 0 (GRIP#5136 — the stall-watchdog fix issue)
const VERIFY_ROUTE = 'local'  // 'local' = on-device ollama, nothing leaves the machine.
                              // 'poolb' = HAL cloud, redacted and fail-closed. Use 'local'
                              // whenever the corpus is private.
const SHIP = false            // true chains /ultrawin. Read-only default is deliberate.
// IN-RUN TOKEN CEILING (GRIP#6977). Counted in OUTPUT tokens, because that is what
// budget.spent() reports, so it is far smaller than the total-token estimate that
// grip-fanout-preflight prints, most of which is input. Measured from this run's start,
// because budget.spent() also counts the main session and any other workflow this turn.
// Checked BEFORE each converge round: a round already started is not interrupted, so a run
// can pass the line by up to one round's spend. The only recorded fleet cost
// (lib/fanout_preflight.py) is in total tokens, so 1_000_000 is a starting point, not a
// calibration -- the return carries tokens_spent so each run can set the next one's ceiling.
const TOKEN_CEILING = 1_000_000
// Held back for the phases that always run after the loop, Verify and then Synthesis. No
// converge round may START once this run has spent TOKEN_CEILING - VERIFY_RESERVE.
const VERIFY_RESERVE = 150_000
const FINDINGS_PER_LANE = 60  // per lane id, across the whole run. Findings past it are
                              // counted and logged, never silently dropped. It also bounds
                              // how much the verify driver has to write out.
const BACKLOG_CAP = 50        // unanswered questions kept verbatim for the return. Every one
                              // is counted, so a full backlog still shows its overflow.
// A LABEL, NOT A SCHEDULE. This is interpolated into each round's prompt as
// "re-dispatch depth N" and printed in a log line. It gates nothing: it spaces no rounds,
// caps no fan-out, and does not influence the loop, which advances `depth += 1` per round
// and is bounded by DEPTH_BUDGET alone. It was described as "Fibonacci-spaced depths" in
// this file and in SKILL.md until 2026-09-01, when a peer session preparing to port it as a
// scheduling primitive asked what the numbers indexed and the honest answer turned out to be
// "nothing". Kept because a lane knowing how deep it is re-dispatched appears to help it
// judge novelty — but that is a prompt-content hypothesis, unmeasured, and if you want real
// Fibonacci spacing you are designing it rather than copying it.
const FIB_LABEL = [1, 1, 2, 3, 5, 8, 13]

const REPO = '/absolute/path/to/the/repo'        // REQUIRED EDIT
const SCRATCH = '/absolute/path/to/scratch/run'  // REQUIRED EDIT
const GRIP = '~/.grip'                 // where lib/fleet_verify.py lives

// ---------------------------------------------------------------------------
// SCHEMAS — `learned` and `questions` are the two cross-learning channels.
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
          // KEEP THIS DESCRIPTION SHORT. v1 wrote a long prose enumeration here and several
          // lanes wrote prose BACK, producing bearings like "egress-path — LIVE CODE, DORMANT
          // PROCESS. The `LlamaEngine` imported at ...". A field that asks for one token must
          // look like it asks for one token. lib/fleet_verify.normalise_bearing() repairs the
          // damage, but the schema should not cause it.
          bearing: { type: 'string', description: 'ONE token from the caller-defined set. No prose, no explanation.' },
        },
      },
    },
    learned: {
      type: 'array',
      items: { type: 'string' },
      description: 'CROSS-LEARNING CHANNEL 1 — vocabulary. New symbol names, aliases, paths, config keys or spellings YOU discovered that another lane should sweep for. Empty is valid and honest; never invent terms to look productive.',
    },
    questions: {
      type: 'array',
      items: { type: 'string' },
      description: 'CROSS-LEARNING CHANNEL 2 — what you could NOT resolve from your own slice but another lane probably can. Phrase each as a direct question naming what would answer it ("does anything READ cursor.json on restart, or only write it?"). These drive the next round: a round with no questions ends the loop. Empty is valid — do not manufacture questions to keep the loop alive.',
    },
    searched: {
      type: 'string',
      description: 'the EXACT scope covered: roots, file counts by type, the method used, and anything that could NOT be searched. A negative result is only as good as this statement.',
    },
  },
}

// What the verify driver hands back: one verdict per finding id, copied from the checker's
// output file. The run joins these onto its findings by id (GRIP#6975), so success can be
// decided from what the checker said rather than from what discovery claimed.
const VERIFY_REPORT = {
  type: 'object',
  required: ['verdicts', 'searched'],
  properties: {
    verdicts: {
      type: 'array',
      items: {
        type: 'object',
        required: ['id', 'verdict'],
        properties: {
          id: { type: 'string', description: 'the finding id exactly as it appears in findings-verified.json' },
          verdict: { type: 'string', enum: ['CONFIRMED', 'WEAK', 'REFUTED', 'UNCHECKED'] },
        },
      },
    },
    searched: {
      type: 'string',
      description: 'the checker summary block verbatim, the command exit code, and the id and reasoning of every REFUTED finding',
    },
  },
}

// ---------------------------------------------------------------------------
// COMMON — ultrathink is now unconditional.
// ---------------------------------------------------------------------------
const COMMON = `/ultrathink

THINK HARD. Before you conclude anything: state what would falsify it, check that your scope
matches your evidence, and prefer "UNESTABLISHED, and here is exactly what would settle it"
over a confident guess. A wrong confident answer is far more expensive than a slow one,
because someone will build on it. Where you are uncertain, say so on that specific item
rather than resolving it silently.

You are one lane of a convergent multi-agent sweep over ${REPO}.
THE QUESTION: <<<EDIT ME — the one question the whole fleet is answering>>>

HARD RULES:
- LOCAL ONLY: no WebSearch, no WebFetch, no network. Read + Grep + Bash only.
- Never read dependency or build directories (node_modules, dist, build, .next, vendor, target).
- ripgrep: use \`rg -n\`, NEVER \`rg -r\` (that is --replace and silently rewrites your output).
  Gitignored content needs \`rg --no-ignore-vcs\`.
- macOS ships no GNU \`timeout\`; bound long children with python3 subprocess.run(timeout=N).
- zsh: \`status\` is a read-only special variable — name loop vars rc/st instead.
- Scratch space: ${SCRATCH} (mkdir -p your own subdirectory).
- Report only what you can point at. Quote verbatim, keep quotes short, and never paraphrase a
  quote into something the file does not say. An unscoped negative is not a finding.
- Distinguish DOCUMENTED from IMPLEMENTED from OBSERVED RUNNING. A rule describing a pattern
  is not an implementation, and a code path with no caller never runs.
- "bearing" takes ONE token. Put your explanation in the quote or the finding, never there.
Return via StructuredOutput.`

// ---------------------------------------------------------------------------
// State + merge helpers.
// ---------------------------------------------------------------------------
const all = []
const scope = []
const seen = new Set()
let learned = new Set()
let questions = []
const perLane = {}         // lane id -> findings kept so far (FINDINGS_PER_LANE)
const overLaneCap = {}     // lane id -> findings refused by FINDINGS_PER_LANE, counted
const backlog = []         // questions never put to any lane, at most BACKLOG_CAP
let backlogTotal = 0       // every such question, kept verbatim or not

const keyOf = (f) => `${f.locator}|${(f.quote || '').slice(0, 40)}`

// What THIS run has spent, in output tokens (GRIP#6977). Two ceilings, and either must leave
// Verify its reserve: this run's own, and the turn's "+N" target when the operator set one
// (budget.remaining() is Infinity without one, so that half is then inert). Past a "+N"
// target the runtime makes agent() THROW, and Verify is a bare agent() call, so a loop
// allowed to spend into the reserve would kill Verify outright.
const SPENT_AT_START = budget.spent()
const runSpent = () => budget.spent() - SPENT_AT_START
const ceilingReached = () =>
  runSpent() >= TOKEN_CEILING - VERIFY_RESERVE || budget.remaining() <= VERIFY_RESERVE

const toBacklog = (items, why) => {
  backlogTotal += items.length
  const room = Math.max(0, BACKLOG_CAP - backlog.length)
  backlog.push(...items.slice(0, room).map(({ q, from }) => ({ q, from, why })))
}

// `label` is what a person reads -- the agent's display label, which in a converge round is
// r<n>:<lane>. `laneId` is WHICH LANE it was, and it never changes (GRIP#6978). Routing keys
// on the lane id: it keyed on the label until 2026-10-02, so from the first converge round
// on, "never route a question back to its asker" compared lane1 with r6:lane1, never
// matched, and handed lanes their own questions.
const absorb = (r, label, laneId = label) => {
  if (!r) { scope.push(`${label}: AGENT FAILED — scope NOT covered`); return 0 }
  scope.push(`${label}: ${r.searched || 'NO SCOPE STATEMENT — treat this lane as uncovered'}`)
  learned = new Set([...learned, ...(r.learned || [])])
  questions.push(...(r.questions || []).map(q => ({ q, from: laneId, label })))
  const fresh = (r.findings || []).filter(f => !seen.has(keyOf(f)))
  fresh.map(keyOf).map(k => seen.add(k))
  // FINDINGS_PER_LANE is counted per lane id, so it holds across rounds (GRIP#6977).
  const kept = fresh.slice(0, Math.max(0, FINDINGS_PER_LANE - (perLane[laneId] || 0)))
  perLane[laneId] = (perLane[laneId] || 0) + kept.length
  if (kept.length < fresh.length) {
    overLaneCap[laneId] = (overLaneCap[laneId] || 0) + fresh.length - kept.length
    log(`${label}: ${fresh.length - kept.length} finding(s) past FINDINGS_PER_LANE (${FINDINGS_PER_LANE}) for ${laneId}, counted and not kept`)
  }
  // `id` is the run's own handle on a finding, f1, f2, ... in absorb order. It is what the
  // checker's verdicts are joined back onto after Verify (GRIP#6975), and it is written last
  // so an agent that returns its own `id` field cannot choose which verdict it receives.
  all.push(...kept.map((f, i) => ({ ...f, lane: label, lane_id: laneId, id: `f${all.length + i + 1}` })))
  return kept.length
}

/** Route each question to the lane whose slice can answer it.
 *
 * Deliberately keyword-based rather than model-based: routing is loop control, and paying a
 * model call to decide who answers a question would cost more than just asking every lane.
 * A question matching no lane's keywords goes to the lane that did NOT raise it, so an
 * unroutable question is still asked by someone other than its author — which is the whole
 * point of routing it at all.
 *
 * `item.from` is the asker's permanent lane id, never a round label, so the asker is
 * excluded in every round and not only the first (GRIP#6978).
 */
const routeQuestion = (item, lanes, keywords) => {
  const q = item.q.toLowerCase()
  const hit = lanes.find(l => l !== item.from && (keywords[l] || []).some(k => q.includes(k)))
  // No `|| lanes[0]` fallback. That returned the RAISING lane whenever it was the only lane,
  // handing a lane back the question it just said it could not answer — which burns a round
  // and looks like routing. With one lane there is nobody else to ask, so the honest result
  // is null and the caller drops the question rather than pretending it was routed.
  return hit || lanes.find(l => l !== item.from) || null
}

// ---------------------------------------------------------------------------
// PHASE 1 — SCOUT MATERIALISES THE SLICES.
// ---------------------------------------------------------------------------
phase('Scout')
const scout = await spawn(`${COMMON}
TASK: build the working set in ${SCRATCH}/slices/ (mkdir -p). Write ONE file per downstream
lane so each lane reads a prepared slice instead of re-walking the corpus.
<<<EDIT ME — one numbered slice per lane, each naming exactly what goes in it>>>
REPORT the line count of every file explicitly and put those counts in "searched" — downstream
lanes inherit them as the scope their negatives rest on. Return findings=[] (this is the
inventory step); put every alias, path and key you noticed into "learned", and every thing you
noticed but could not resolve into "questions".`,
  { label: 'scout', phase: 'Scout', schema: FINDINGS, effort: 'high', stallMs: STALL_MS })

absorb(scout, 'scout')

// ---------------------------------------------------------------------------
// PHASE 2 — LANES.
// ---------------------------------------------------------------------------
phase('Lanes')
const _LANES_RAW = ['lane1', 'lane2', 'lane3', 'lane4']   // EDIT: name your slices
if (_LANES_RAW.length > GRIP_MAX_AGENTS) {
  log(`GRIP_MAX_AGENTS cap (${GRIP_MAX_AGENTS}): dropped ${_LANES_RAW.length - GRIP_MAX_AGENTS} lane(s)`)
}
const LANES = _LANES_RAW.slice(0, GRIP_MAX_AGENTS)
const KEYWORDS = {                                    // EDIT: routing vocabulary per lane
  lane1: ['<<<keyword>>>'],
  lane2: ['<<<keyword>>>'],
  lane3: ['<<<keyword>>>'],
  lane4: ['<<<keyword>>>'],
}
const LANE_BRIEF = {
  lane1: `READ ${SCRATCH}/slices/lane1.txt IN FULL, then <<<EDIT ME>>>`,
  lane2: `READ ${SCRATCH}/slices/lane2.txt IN FULL, then <<<EDIT ME>>>`,
  lane3: `READ ${SCRATCH}/slices/lane3.txt IN FULL, then <<<EDIT ME>>>`,
  lane4: `READ ${SCRATCH}/slices/lane4.txt IN FULL, then <<<EDIT ME>>>`,
}

const laneResults = await parallel(LANES.map(l => () =>
  spawn(`${COMMON}
LANE ${l.toUpperCase()}. ${LANE_BRIEF[l]}
Fill "questions" with what you could not resolve from your own slice but another lane can.
searched: the line count of your slice and what you deliberately discarded.`,
    { label: l, phase: 'Lanes', schema: FINDINGS, effort: 'max', stallMs: STALL_MS })))

laneResults.map((r, i) => absorb(r, LANES[i], LANES[i]))
log(`lanes: ${all.length} findings, ${learned.size} terms, ${questions.length} open questions`)

// ---------------------------------------------------------------------------
// PHASE 3 — CONVERGE, driven by routed questions.
// ---------------------------------------------------------------------------
phase('Converge')

// THE CRITERION — every adaptation MUST write its own. Two properties keep it safe:
//   - never vacuously true: assert each class NON-EMPTY before asserting a property of it,
//     because [].every(...) is true;
//   - never keyed on a class whose true answer may be an ABSENCE, because a scoped negative
//     has no locator to offer, so such a criterion can only be met if the answer is "yes" —
//     which is a bias, not a stopping rule.
// It is evaluated TWICE, so read the `pool` argument and never `all` directly: inside the
// loop over every finding so far, which can only say the search found what it looked for,
// and again after Verify over the CONFIRMED findings alone, which decides whether the run
// succeeded (GRIP#6975).
const criterionMet = (pool = all) => {
  const need = ['<<<bearing-1>>>', '<<<bearing-2>>>']
  return need.every(b => {
    const cls = pool.filter(f => f.bearing === b)
    return cls.length > 0 && cls.every(f => f.locator)
  })
}

const convergeRound = async (n, depthLabel) => {
  const terms = Array.from(learned).slice(0, TERMS_PER_ROUND)
  // Log what the cap DISCARDS. Without this a truncated round is indistinguishable from a
  // dry one: a real run raised 57 questions against a cap of 6, and the 51 dropped left no
  // trace at all, so "the loop went dry" would have been believed. The dropped questions
  // are also KEPT, as unanswered, in the bounded backlog the return reports (GRIP#6977).
  const raised = questions.length
  const unroutable = []
  const routed = questions.slice(0, QUESTIONS_PER_ROUND)
    .map(item => ({ ...item, to: routeQuestion(item, LANES, KEYWORDS) }))
    .filter(r => r.to || (unroutable.push(r), false))
  const overCap = questions.slice(QUESTIONS_PER_ROUND)
  toBacklog(overCap, 'over-round-cap')
  toBacklog(unroutable, 'unroutable')
  if (overCap.length || unroutable.length) {
    log(`round ${n}: ${overCap.length} question(s) dropped by the cap, ${unroutable.length} unroutable -- kept as unanswered`)
  }
  learned = new Set()
  questions = []
  if (!routed.length) return 0

  const byLane = LANES.filter(l => routed.some(r => r.to === l))
  log(`round ${n} (label ${depthLabel}): ${routed.length} of ${raised} questions routed -> ${byLane.length} lanes`)

  const results = await parallel(byLane.map(l => () =>
    spawn(`${COMMON}
CONVERGE ROUND ${n}, LANE ${l.toUpperCase()}, re-dispatch depth ${depthLabel}.
Rounds so far found ${all.length} findings.
QUESTIONS ROUTED TO YOU — every one was raised by a DIFFERENT lane that could not answer it
from its own slice. Answering them is your primary task this round:
${JSON.stringify(routed.filter(r => r.to === l).map(r => `[from ${r.from}] ${r.q}`), null, 1)}
Vocabulary the previous round surfaced, pooled across every lane: ${JSON.stringify(terms)}.
Known locators (return only findings NOT in this list): ${JSON.stringify(Array.from(seen).slice(0, KNOWN_KEYS_IN_PROMPT))}.
Raise new questions ONLY if genuinely unresolved. If you can answer nothing new, return an
empty findings array and an empty questions array — honest dryness ends the loop and is the
right answer.`,
      { label: `r${n}:${l}`, phase: 'Converge', schema: FINDINGS, effort: 'max', stallMs: STALL_MS })))

  return results.reduce((acc, r, i) => acc + absorb(r, `r${n}:${byLane[i]}`, byLane[i]), 0)
}

let depth = 1 + LANES.length
let dry = 0
let satisfied = false
let fibIdx = 0
let stoppedByCeiling = false

// `satisfied` starts false and is evaluated only AFTER a round: a criterion checked up front
// can be met by one lucky lane finding, which skips every round and with it the entire
// cross-learning mechanism this template exists to run.
while (depth < DEPTH_BUDGET && dry < DRY_EXIT && questions.length > 0 && !satisfied) {
  if (ceilingReached()) {
    stoppedByCeiling = true
    log(`token ceiling: ${runSpent()} output tokens spent this run; no round may start past ${TOKEN_CEILING - VERIFY_RESERVE}${budget.total ? ` or with ${VERIFY_RESERVE} or less left of the turn's target` : ''}, so Verify keeps its reserve`)
    break
  }
  depth += 1
  const fresh = await convergeRound(depth, FIB_LABEL[Math.min(fibIdx, FIB_LABEL.length - 1)])
  fibIdx += 1
  dry = fresh ? 0 : dry + 1
  satisfied = criterionMet()
  log(`round ${depth}: +${fresh} fresh (total ${all.length}), questions=${questions.length}, dry=${dry}, criterion=${satisfied}`)
}

// Why the loop stopped, fixed here rather than recomputed at the end, where phases that run
// after the loop could change the counts it reads.
const exitReason = satisfied ? 'criterion'
  : (stoppedByCeiling ? 'token-ceiling'
    : (dry >= DRY_EXIT ? 'dry'
      : (questions.length === 0 ? 'no-open-questions' : 'depth-budget')))

// ---------------------------------------------------------------------------
// PHASE 4 — VERIFY, on Pool B. ONE Pool-A agent drives N non-Anthropic checks.
// ---------------------------------------------------------------------------
phase('Verify')
const strong = all.filter(f => f.bearing !== 'weak')
const findingsPath = `${SCRATCH}/findings-unverified.json`

const verifyReport = await spawn(`${COMMON}
VERIFY PHASE. You do NOT judge findings yourself. You DRIVE a Pool-B verification and report
what came back — the checking is done by non-Anthropic models so that the verifier is never
the same vendor as the author (rules/exogenous-anchor-law.md).

STEP 1. Write ALL ${strong.length} of these findings to ${findingsPath} as {"findings": [...]},
each exactly as given and with its "id", because the run joins the verdicts back by id. Do
not choose among them; the checker's selector does that:
${JSON.stringify(strong)}

STEP 2. Run, and report its exit code and stderr verbatim if it is non-zero:
  ${GRIP}/venv/bin/python -m lib.fleet_verify ${findingsPath} \\
      --route ${VERIFY_ROUTE} --cap ${VERIFY_CAP} --workers ${VERIFY_WORKERS} \\
      --out ${SCRATCH}/findings-verified.json
  (run it from ${GRIP}; route '${VERIFY_ROUTE}' — 'local' keeps every check on-device,
   'poolb' redacts and sends to HAL cloud, failing closed if redaction errors. It checks at
   most ${VERIFY_CAP}, dealt round-robin across bearings, so findings-verified.json holds
   only the findings it chose.)

STEP 3. Read ${SCRATCH}/findings-verified.json. Fill "verdicts" with one {"id", "verdict"} for
EVERY finding in that file, copying its id and its check.verdict exactly. Never infer a
verdict, and never add a row for a finding the file does not contain: a finding with no
verdict is counted UNCHECKED, which is the honest result. If the command failed and the file
does not exist, return an empty "verdicts" array.

REPORT HONESTLY AND DO NOT SMOOTH IT OVER:
- The count of CONFIRMED / WEAK / REFUTED / UNCHECKED. UNCHECKED means the checker itself
  failed on that finding; it is a named gap, never a pass.
- The known limitation of this pass, stated plainly: the checker cannot open the repository,
  so it judges whether each quote actually supports its claim and whether the claim's scope
  exceeds its quote. It CANNOT establish ground truth — it will not catch "this code has no
  caller". A CONFIRMED here means internally coherent, not verified against the tree.
Put the summary block verbatim, and the id and reasoning of every REFUTED finding, in "searched".`,
  { label: 'verify-driver', phase: 'Verify', schema: VERIFY_REPORT, effort: 'max', stallMs: STALL_MS })

absorb(verifyReport, 'verify-driver')

// THE CHECKER'S VERDICTS, JOINED BACK BY FINDING ID (GRIP#6975). A row counts only when its id
// was sent and its verdict is one the checker can give, so a garbled or invented row can never
// manufacture a CONFIRMED; two rows that disagree about one id make it UNCHECKED. A finding with
// no accepted verdict is UNCHECKED -- a named gap, never a pass.
const VERDICTS = ['CONFIRMED', 'WEAK', 'REFUTED', 'UNCHECKED']
const sent = new Set(strong.map(f => f.id))
const verdictOf = new Map()
for (const row of (verifyReport && verifyReport.verdicts) || []) {
  if (!row || !sent.has(row.id) || !VERDICTS.includes(row.verdict)) continue
  const prior = verdictOf.get(row.id)
  verdictOf.set(row.id, prior && prior !== row.verdict ? 'UNCHECKED' : row.verdict)
}
all.forEach(f => { f.verdict = verdictOf.get(f.id) || 'UNCHECKED' })

const confirmed = strong.filter(f => f.verdict === 'CONFIRMED')
const countOf = (v) => strong.filter(f => f.verdict === v).length
const verification = {
  sent: strong.length,
  confirmed: confirmed.length,
  weak: countOf('WEAK'),
  refuted: countOf('REFUTED'),
  unchecked: countOf('UNCHECKED'),
  // The part of `unchecked` that got no verdict at all: left out by the selector under
  // VERIFY_CAP, or never reported back. The rest of `unchecked` are checks that ran and failed.
  no_verdict: strong.filter(f => !verdictOf.has(f.id)).length,
}

// THREE ANSWERS, KEPT APART (GRIP#6975). The loop's criterion ran over UNVERIFIED findings, so
// all it can say is that the search found what it was looking for. Whether that answer is
// ESTABLISHED is decided again here, over CONFIRMED findings alone, and a change is AUTHORISED
// only when there is CONFIRMED evidence to justify it. Ship is gated on that in code below,
// not by a sentence in its prompt.
const searchFinished = satisfied
const answerEstablished = confirmed.length > 0 && criterionMet(confirmed)
const changeAuthorised = SHIP && confirmed.length > 0
log(`verify: ${verification.confirmed} confirmed, ${verification.refuted} refuted, ${verification.weak} weak, ${verification.unchecked} unchecked (${verification.no_verdict} with no verdict) of ${strong.length}; search finished=${searchFinished}, answer established=${answerEstablished}`)

// ---------------------------------------------------------------------------
// PHASE 5 — SYNTHESIS.
// ---------------------------------------------------------------------------
phase('Synthesis')
const synthesis = await spawn(`${COMMON}
SYNTHESIS. You open NO new search. You reason over what survived refutation.
Read ${SCRATCH}/findings-verified.json yourself rather than trusting a summary. It holds only
the findings the checker chose; ${verification.no_verdict} more, in ${findingsPath}, got no
verdict and are UNCHECKED -- name them as unchecked, never use them as evidence.
SCOPE STATEMENTS — every negative you state must rest on one of these, and a lane recorded as
FAILED means that scope is UNCOVERED, not clean: ${JSON.stringify(scope, null, 1)}
TASK: answer the fleet's question end to end, every claim carrying its locator. Mark anything
the evidence does not settle as UNESTABLISHED rather than bridging it with a plausible guess.
Name what ALREADY EXISTS that removes work — on real runs that is the highest-value output.
End with what remains open and exactly what would settle each item.
Put the answer in "searched" and any NEW finding in "findings".`,
  { label: 'synthesis', phase: 'Synthesis', schema: FINDINGS, effort: 'max', stallMs: STALL_MS })

absorb(synthesis, 'synthesis')

// ---------------------------------------------------------------------------
// PHASE 6 — SHIP (optional). Off by default: the read-only default is what makes this
// template safe to point at a corpus nobody has cleared for writing.
// ---------------------------------------------------------------------------
// Ship needs SHIP on AND at least one CONFIRMED verdict (GRIP#6975). Until 2026-10-02 the only
// gate was the sentence in the prompt below, so a run whose every check failed still started
// an implement-and-merge agent and trusted it to notice.
let shipReport = null
if (changeAuthorised) {
  phase('Ship')
  shipReport = await spawn(`${COMMON}
SHIP PHASE (/ultrawin). Take ONLY the CONFIRMED findings in ${SCRATCH}/findings-verified.json
and implement the change they justify. The run counted ${confirmed.length} CONFIRMED:
${JSON.stringify(confirmed.map(f => ({ id: f.id, locator: f.locator })))}
If the file disagrees with that list, ship nothing and report the disagreement.
Follow /ultrawin: implement, test, open a PR with a
plain-language body, drive the AGORA convergence loop, and merge only on a PASS with at least
two sitting seats and no REQUEST_CHANGES.
NEVER ship on a WEAK, REFUTED or UNCHECKED finding. If the file holds no CONFIRMED finding,
ship nothing and say so -- that is a correct outcome, not a failure.
Put what you shipped (branch, commit SHA, PR URL) in "searched".`,
    { label: 'ship', phase: 'Ship', schema: FINDINGS, effort: 'max', stallMs: STALL_MS })
  absorb(shipReport, 'ship')
} else if (SHIP) {
  log('ship: NOT started -- Verify returned no CONFIRMED finding, so no change is authorised')
}

// ---------------------------------------------------------------------------
// RESULT
// ---------------------------------------------------------------------------
// Whatever is still open was never put to any lane: the loop stopped first, or it was raised
// by a phase after the loop. It joins the backlog as unanswered (GRIP#6977).
toBacklog(questions, 'still-open')

return {
  // Every finding carries its verdict; one never checked, or added after Verify, is UNCHECKED.
  findings: all.map(f => ({ ...f, verdict: f.verdict || 'UNCHECKED' })),
  verified_path: `${SCRATCH}/findings-verified.json`,
  verify_route: VERIFY_ROUTE,
  verification,
  // Three answers, never one (GRIP#6975). `success` IS answer_established -- a single headline
  // field, so no caller reads a finished search as an established answer. This replaces
  // `criterion_met`, which reported the search's criterion under a success-sounding name.
  success: answerEstablished,
  search_finished: searchFinished,
  answer_established: answerEstablished,
  change_authorised: changeAuthorised,
  shipped: !SHIP ? 'not requested'
    : (!changeAuthorised ? 'refused: Verify returned no CONFIRMED finding'
      : (shipReport ? 'see ship scope statement' : 'SHIP PHASE FAILED')),
  rounds_used: depth,
  depth_budget: DEPTH_BUDGET,
  tokens_spent: runSpent(),     // output tokens, this run only -- calibrates the next ceiling
  token_ceiling: TOKEN_CEILING,
  over_lane_cap: overLaneCap,   // lane id -> findings refused by FINDINGS_PER_LANE
  open_questions: questions.length,
  // Every question no lane was ever asked, with why: 'over-round-cap', 'unroutable' or
  // 'still-open'. `kept` holds at most BACKLOG_CAP of them; `total` counts them all.
  unanswered_questions: { total: backlogTotal, kept: backlog },
  exit_reason: exitReason,
  searched: scope,
}
