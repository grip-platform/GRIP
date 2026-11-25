// /infiltrate — copy-adaptable release-readiness infiltration.
//
// HOW TO USE: copy this file, edit the two blocks marked EDIT ME (TARGETS and
// LANES), then pass it to the Workflow tool via {script: "<contents>"} or save
// it and pass {scriptPath: "<path>"}. Everything below the lane list is the
// machinery and does not need changing.
//
// Design notes that are load-bearing, not decoration:
//   - pipeline(), not parallel() between stages: a lane's findings falsify the
//     moment that lane returns. A barrier here costs the gap between the
//     slowest and fastest lane and buys nothing.
//   - The falsifier defaults to REFUTED. An auditor rewarded for findings
//     inflates them; the defence is a hostile second reader, not an exhortation.
//   - evidence, scope_searched and unchecked are REQUIRED schema fields. A
//     requirement in a prompt is advice; a requirement in a schema is a wall.
//   - `node --check` will reject the bare top-level `return` at the end of
//     this file. That is a FALSE POSITIVE, not a bug: the Workflow runtime
//     wraps the script in an async function, so a top-level return and
//     top-level await are both correct. Validate by wrapping in an async
//     IIFE before checking. Do not 'fix' the return.
//   - Name the files a lane may read, or cap the bytes. "Read any doc that
//     looks like marketing" is an unbounded read and will kill an agent on a
//     large changelog.

export const meta = {
  name: 'infiltrate',
  description: 'Read-only release-readiness infiltration: audit lanes, adversarial falsification of every finding, per-target verdict',
  phases: [
    { title: 'Infiltrate', detail: 'one read-only auditor per release-readiness failure mode' },
    { title: 'Falsify', detail: 'independent agents default to REFUTED and try to kill each finding' },
    { title: 'Synthesise', detail: 'per-target verdict assembled from findings that survived' },
  ],
}

// ─── EDIT ME 1: the targets ────────────────────────────────────────────────
// Measure scale FIRST and put the real numbers here. Scale asymmetry inside a
// suite is itself a finding, and the agents reason better with the figures.
const TARGETS = 'Targets: <NAME> at <ABS PATH> (<n> files, <n> tests, <n> CI workflows, <n> commits, last commit <date>).'

// Paths that are off limits. Name them; do not rely on judgement.
const FORBIDDEN = '<abs path to any private message mirror, client record, or personal-matter directory>'
// ───────────────────────────────────────────────────────────────────────────

const RULES = [
  'HARD CONSTRAINTS — this is a stealth infiltration audit, zero footprint:',
  '- READ-ONLY. Create, modify, delete, commit, push NOTHING. Use cat / head / sed -n / rg / grep / ls / git log / git show / git ls-files. Running an existing test read-only is fine; writing files is not. Do not run any command containing a redirect, tee, rm, mv, cp, git add, git commit, git checkout, or git stash.',
  '- PRIVACY: never read ' + FORBIDDEN + '. If you encounter personal or client data anywhere, report ONLY that it exists and its path — never its content.',
  '- Never read node_modules, venv, .venv, dist, build, __pycache__, .next, target, vendor, .git/objects.',
  '- EVERY finding carries evidence: an exact file:line, or a command plus its ACTUAL output. A finding with no evidence is not a finding — drop it rather than assert it.',
  '- A negative claim is only valid if you NAME THE SCOPE you searched. Use rg --no-ignore-vcs for anything that might be gitignored; plain rg is blind to gitignored paths.',
  '- Judge severity honestly. blocks_release = true means a stranger would hit real harm, data loss, a security hole, or a claim that is flatly false. It does not mean "this could be nicer".',
  '- Report STRENGTHS too. An audit that finds only faults is not measuring, it is performing.',
  '- Answer from the STRANGER POSITION: report what the shipping surface alone establishes, not what you inferred from an hour of reading.',
  '',
  'BOUNDED READS — mandatory. Three lanes of this skill\'s own first run died for want of this rule.',
  '- NEVER cat a whole file without checking its size first. A 233KB changelog is ~58k tokens and will kill a lane on its own.',
  '- Use rg with line numbers and sed -n RANGES. Read at most ~200 lines of any single file at a time.',
  '- Budget roughly 25 file reads. Spend them on the highest-stakes questions and declare the rest in "unchecked".',
  '- Four well-evidenced findings beat dying at twenty.',
].join('\n')

const FINDINGS_SCHEMA = {
  type: 'object',
  properties: {
    dimension: { type: 'string' },
    scope_searched: { type: 'string', description: 'exact paths, globs and commands you actually covered' },
    findings: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          title: { type: 'string' },
          repo: { type: 'string' },
          evidence: { type: 'string', description: 'file:line, or command plus its real output' },
          severity: { type: 'string', enum: ['blocker', 'major', 'minor'] },
          blocks_release: { type: 'boolean' },
          falsifier: { type: 'string', description: 'cheapest observation that would prove THIS FINDING wrong' },
          fix_shape: { type: 'string' },
        },
        required: ['title', 'repo', 'evidence', 'severity', 'blocks_release', 'falsifier'],
      },
    },
    strengths: { type: 'array', items: { type: 'string' } },
    unchecked: { type: 'string', description: 'what you did NOT get to — silence must never imply coverage' },
  },
  required: ['dimension', 'scope_searched', 'findings', 'strengths', 'unchecked'],
}

const VERDICT_SCHEMA = {
  type: 'object',
  properties: {
    survives: { type: 'boolean' },
    verdict_reason: { type: 'string' },
    corrected_severity: { type: 'string', enum: ['blocker', 'major', 'minor', 'not-a-defect'] },
    independent_evidence: { type: 'string', description: 'what YOU observed, with the command or file:line you used' },
    auditor_overreach: { type: 'boolean' },
  },
  required: ['survives', 'verdict_reason', 'corrected_severity', 'independent_evidence'],
}

// ─── EDIT ME 2: the lanes ──────────────────────────────────────────────────
// A lane is a FAILURE MODE, not a directory. Keep the four rungs in view:
// Built (code exists) / Bound (shipping config invokes it) / Proven (a test
// goes red when it breaks) / Verifiable-by-a-stranger (a third party can check
// it with only what ships). Most audits only ever ask the first.
//
// Put each rule's CARVE-OUTS in the same breath as the rule, or the lane will
// bury its real findings under legitimate patterns.
//
// Set model: 'fable' on AT MOST ONE lane, and only a lane whose reads you have
// bounded. Measured 2026-09-20 on this skill's own first run: three concurrent
// fable lanes ALL died together — 8 rate_limit_error and 15 usage-limit events
// each, transcripts at ~6.8MB, then autocompact thrashing and invalid_request.
// A prior note here said "no more than about three" on the strength of an
// earlier five-lane failure. Three was still too many, so the number is now one.
// The two causes compound and must both be handled: concurrent lanes share one
// model quota, and an unbounded read fills a context regardless of quota. Put
// the byte caps in the lane prompt (see BOUNDED READS below), not in your head.
const LANES = [
  { key: 'install', prompt: 'AUDIT DIMENSION: install and first run, from a stranger position.\n<...>' },
  { key: 'claim-integrity', model: 'fable', prompt: 'AUDIT DIMENSION: do the outward claims survive contact with the code?\n<...>' },
  { key: 'leak-surface', prompt: 'AUDIT DIMENSION: what leaks if this goes public tomorrow? Audit git ls-files, not the working tree. Report path and CLASS only, never the secret itself.\n<...>' },
  { key: 'test-truth', prompt: 'AUDIT DIMENSION: do the tests go red when the thing breaks?\n<...>' },
  { key: 'gate-integrity', prompt: 'AUDIT DIMENSION: which controls are BOUND, which fail open, which can block their own repair?\n<...>' },
  { key: 'verifiability', prompt: 'AUDIT DIMENSION: can a STRANGER verify the integrity claim, or must they trust us?\n<...>' },
  { key: 'stubs-and-inert', prompt: 'AUDIT DIMENSION: what ships that names a capability it does not provide?\n<...>' },
  { key: 'deps-supply-chain', prompt: 'AUDIT DIMENSION: pins, undeclared imports, unaudited tree, floating CI actions, licences.\n<...>' },
  { key: 'docs-drift', prompt: 'AUDIT DIMENSION: does the documentation describe the code that exists? Rank by blast radius.\n<...>' },
  { key: 'cross-repo-coherence', prompt: 'AUDIT DIMENSION: do the repos in this suite actually fit together? Spec vs implementation, duplicated catalogues, import direction, what breaks if only one is installed.\n<...>' },
  { key: 'structural-soundness', prompt: 'AUDIT DIMENSION: do the load-bearing metaphors map RELATIONS or only surfaces? Gentner structure-mapping: systematic, one-to-one. Verdict SOUND / PARTIAL / SURFACE, and where PARTIAL name exactly which relation fails to transfer.\n<...>' },
  { key: 'claim-settlement', prompt: 'AUDIT DIMENSION: if the project claims it verifies its own predictions, count how many past-deadline predictions are actually settled, and by a deterministic probe rather than a self-assessment.\n<...>' },
]
// ───────────────────────────────────────────────────────────────────────────

phase('Infiltrate')
log('Infiltrating ' + LANES.length + ' lanes — read-only, zero footprint')

const results = await pipeline(
  LANES,
  (d) => agent(
    RULES + '\n' + TARGETS + '\n\n' + d.prompt + '\n\nReturn findings via the structured output tool. Be specific, be evidenced, and name what you did not check.',
    { label: 'audit:' + d.key, phase: 'Infiltrate', schema: FINDINGS_SCHEMA, model: d.model }
  ),
  (audit, d) => {
    if (!audit || !audit.findings || !audit.findings.length) return { dimension: d.key, audit: audit, verified: [] }
    const top = audit.findings.slice(0, 6)
    return parallel(top.map((f, i) => () =>
      agent(
        RULES + '\n' + TARGETS + '\n\nYou are an ADVERSARIAL FALSIFIER. Your default verdict is REFUTED. This finding survives ONLY if you independently reproduce its evidence and then fail to kill it.\n\n'
        + 'FINDING: ' + f.title + '\nREPO: ' + f.repo + '\nCLAIMED EVIDENCE: ' + f.evidence + '\n'
        + 'CLAIMED SEVERITY: ' + f.severity + ' (blocks release: ' + f.blocks_release + ')\n'
        + 'THE FINDING OWN STATED FALSIFIER: ' + f.falsifier + '\n\n'
        + '1. Go to the claimed evidence yourself. Does that file:line exist and say what was claimed? Quote what is actually there.\n'
        + '2. Run the finding own stated falsifier. Report whether it fires.\n'
        + '3. Hunt the context the auditor missed: a guard elsewhere, a documented carve-out, a deliberate design decision, an existing test, a newer commit that already fixed it, or a rule that sanctions the pattern.\n'
        + '4. Re-grade severity independently. Auditors inflate.\n\n'
        + 'REFUTE if the evidence does not exist, does not say what was claimed, is handled elsewhere, falls inside a carve-out, or is true-but-trivial dressed as a blocker. Set auditor_overreach true when severity was inflated even if the finding survives. Set corrected_severity to not-a-defect when the thing works as designed.',
        { label: 'falsify:' + d.key + ':' + i, phase: 'Falsify', schema: VERDICT_SCHEMA }
      ).then((v) => (v ? { finding: f, verdict: v, dimension: d.key } : null))
    )).then((verified) => ({ dimension: d.key, audit: audit, verified: verified.filter(Boolean) }))
  }
)

const clean = results.filter(Boolean)
const allVerified = clean.flatMap((r) => r.verified)
const survivors = allVerified.filter((v) => v.verdict.survives && v.verdict.corrected_severity !== 'not-a-defect')
const killed = allVerified.filter((v) => !v.verdict.survives || v.verdict.corrected_severity === 'not-a-defect')
const blockers = survivors.filter((v) => v.verdict.corrected_severity === 'blocker')
const overreach = allVerified.filter((v) => v.verdict.auditor_overreach)

log('Falsified: ' + survivors.length + ' survived, ' + killed.length + ' refuted, ' + blockers.length + ' blockers, ' + overreach.length + ' overreach')

phase('Synthesise')

const dossier = survivors.map((v) => '[' + v.verdict.corrected_severity + '] (' + v.finding.repo + ') ' + v.finding.title + '\n    evidence: ' + v.finding.evidence + '\n    independently confirmed: ' + v.verdict.independent_evidence).join('\n')
const strengthText = clean.flatMap((r) => (r.audit && r.audit.strengths) || []).join('\n- ')
const uncheckedText = clean.map((r) => r.dimension + ': ' + ((r.audit && r.audit.unchecked) || 'not reported')).join('\n')
const killedText = killed.map((v) => 'REFUTED: ' + v.finding.title + ' — ' + v.verdict.verdict_reason).join('\n')

const verdict = await agent(
  TARGETS + '\n\nYou are writing the RELEASE-READINESS VERDICT. ' + LANES.length + ' read-only lanes ran; every finding was handed to an independent agent whose default verdict was REFUTED. ' + killed.length + ' were killed; ' + survivors.length + ' survived.\n\n'
  + 'SURVIVED FALSIFICATION:\n' + (dossier || '(none survived — state that plainly rather than softening it)') + '\n\n'
  + 'KILLED (do not resurrect; note if auditors inflated systematically in one direction):\n' + (killedText || '(none)') + '\n\n'
  + 'STRENGTHS:\n- ' + (strengthText || '(none recorded)') + '\n\n'
  + 'NOT COVERED (per-lane self-report — this is the honest limit of the audit and MUST appear in the verdict):\n' + uncheckedText + '\n\n'
  + 'Requirements: a separate one-sentence READY / NOT-READY / READY-WITH-CARVE-OUTS call per target; then blockers ranked by what a stranger hits FIRST, each with evidence and the smallest fix; then non-blocking gaps that would embarrass the suite in front of a technical buyer; then what is genuinely strong, with evidence; then the limits, stated plainly so silence never implies coverage. Plain-spoken, no hype, no softening, no inflation. Where a finding contradicts a claim the project makes about itself, say so explicitly — that is the most valuable output here.',
  { label: 'verdict', phase: 'Synthesise', effort: 'high' }
)

const critic = await agent(
  'Completeness critic on a release-readiness audit. The verdict:\n\n' + verdict + '\n\nWhat each lane admitted it did NOT check:\n' + uncheckedText + '\n\n'
  + 'Name what is MISSING; do not re-audit. (1) Which release-readiness dimension was never run at all — performance and load, upgrade and migration, data loss on failure, concurrency, observability, incident response, backup and restore, uninstall, legal exposure, or the threat model of a HOSTILE user rather than a careless one? (2) Which surviving finding rests on evidence asserted rather than reproduced? (3) What is the single highest-value thing a follow-up run should do first, and why that one? At most 5 gaps. This becomes the next run work list.',
  { label: 'critic', phase: 'Synthesise' }
)

return {
  counts: { lanes: LANES.length, returned: clean.length, falsified: allVerified.length, survived: survivors.length, refuted: killed.length, blockers: blockers.length, overreach: overreach.length },
  blockers: blockers.map((v) => ({ repo: v.finding.repo, title: v.finding.title, evidence: v.finding.evidence, confirmed: v.verdict.independent_evidence, fix: v.finding.fix_shape })),
  survivors: survivors.map((v) => ({ repo: v.finding.repo, sev: v.verdict.corrected_severity, title: v.finding.title, evidence: v.finding.evidence })),
  verdict: verdict,
  critic: critic,
}
