# Verbind -- a coined slash verb becomes a binding, through a Koppel round

**Status:** contract, ratified by V>> in a /hitl round on 2026-10-03 (four decisions below).
Implementable by any harness; this document carries enough detail to build from.

## 1. What it is, in one paragraph

The operator types a slash verb GRIP does not know, inside a prompt that explains what they
want: `/shipwreck the stale PRs`. Today that verb is plain text. Under Verbind it becomes a
**verbinding** -- the verb bound to a meaning expressed in existing, already-authorised
operations -- and the binding is applied in the same response. The first binding is ALWAYS made
with the operator in the loop, in a **Koppel** round (Afrikaans: *koppel* = couple, connect; it
names the human-AI connection through the /hitl brainstorming moat). From the second
independent use the binding can be promoted to a real skill, automatically, once it has been
tested, monitored and ultralearned. "Verbind" is Afrikaans and Dutch for *connect*, and reads as
"verb binding" in English; a *verbinding* is a connection.

## 2. The four decisions (V>> 2026-10-03, not reopened by later sessions)

| Fork | Decision |
|---|---|
| Name | Feature **Verbind**; the HITL round **Koppel**; artefact **verbinding** |
| First use | A binding at N=1, created through a Koppel round every time; a skill at **N=2**. YSH is decreased by one: *proven 2x, abstract it now.* The first implementation is always /hitl, then tested, monitored, ultralearned, and promoted automatically only if found to be of high quality, value and usefulness |
| FINALLY | Surface and refine on demand: every path seals its decision and prints one line; `/verbind refine` corrects it. A brake, not a gate. Never asks the same thing twice |
| v1 scope | One leading unknown verb per prompt, bounded argument span, bound only to existing read-only operations |

## 3. The gate (converged over 11 forced passes, 2026-10-03)

```
fun verbind(prompt, envelope):                 # envelope = the harness adapter's trusted invocation record (origin, grants, session)
    verb, args = LEADING_UNKNOWN_VERB(prompt)  # L0: known verb or no verb -> PASS at zero cost; v1 = one leading verb, bounded arg span
    if verb is None: return PASS
    if UNRESOLVED(verb): return PASS           # the operator deferred this verb before -- never ask the same thing twice

    outcome, meaning = EXHAUSTED, NONE         # sealed by FINALLY; every path below reassigns outcome to what it actually did
    authority = REFUSED                        # fail closed: a floor that crashes, here or below, authorises nothing
    b = LOOKUP(verb)                           # idempotent: a bound verb never re-infers (f.f = f, zero provider calls)
    if b:
        TRY authority = CAPABILITY_FLOOR(envelope) CATCH any: authority = REFUSED   # a binding fixes MEANING, never AUTHORITY: re-checked on every use
        if not authority.ok: outcome = DENIED; return PASS   # the binding stays; the refused use applies nothing and counts nothing
        b.uses += INDEPENDENT(prompt, b)       # +1 only for an independent task: not a retry, a pasted copy, or model-generated text
        if b.uses >= 2 and QUALITY(b): PROMOTE(b)   # tested + monitored + ultralearned -> /create crystallises a versioned skill
        outcome = BOUND; return APPLY(b, envelope)   # promotion grants no new capability

    TRY
        authority = CAPABILITY_FLOOR(envelope) # deterministic, un-waivable: trusted origin, existing grants, read-only ops in v1
        meaning   = FLOOR_EVIDENCE(prompt, verb, CATALOGUE)   # CF-IOF ranks skills (MAC); SME re-ranks structurally (FAC); evidence only
        pre       = PRE_COUNCIL(meaning) if meaning.uncertain  # one bounded sealed pass; may only make the recommendation MORE cautious
    CATCH any:                                 # extraction, council, kernel, budget exhausted -- a crash never creates
        meaning = NONE                         # authority keeps the floor's answer, or REFUSED if the floor itself crashed
    if not authority.ok: outcome = DENIED; return PASS   # untrusted or ungranted: ask nothing, since no answer could be bound; the verb stays fresh

    # v1: there is NO autonomous create. The evidence pre-fills the Koppel round (recommended default named first).
    for turn in 1..11:                         # one Koppel round, bounded at eleven turns; depth 11 forced
        a = await KOPPEL(verb, prompt, recommended = meaning.top, unclear = meaning.gaps)
        if a.defer:   RECORD_UNRESOLVED(verb); outcome = DEFERRED; return PASS
        if a.refined:                          # the human decides MEANING; the floor already decided AUTHORITY above
            b = await VERBIND(verb, a.meaning) # verbinding @N=1: invocation-local interpretation + immutable sealed receipt
            outcome = RATIFIED; return APPLY(b, envelope)
    RECORD_UNRESOLVED(verb); return PASS          # outcome stays EXHAUSTED: eleven turns, no answer

FINALLY:                                       # every path past L0, always; the two L0 returns at the top cost nothing and seal nothing
    SEAL(outcome, meaning, b, envelope.ref, versions)   # the outcome actually taken, never a default; replay re-runs it
    SURFACE("/verb -> bound to X because <mapping>; `/verbind refine` to correct")   # one line, non-blocking
```

Nine clauses, each pinned by a test in `tests/test_verbind_gate.py`: no infinite loop; a crash
never creates; the Koppel round is bounded at eleven turns; a deferred or bound verb is never asked
again; a bound verb is O(1) and idempotent; the council layer only tightens; harness independence
through the adapter; every path past L0 seals the outcome it actually took; and the authority floor
is checked before any question and on every use of a binding, and fails closed when it crashes.

A denial belongs to the invocation, not to the verb. It is sealed as DENIED and changes nothing in
the vocabulary: a fresh verb refused stays fresh, so the operator can still be asked about it from a
trusted origin, and a bound verb refused stays bound. An earlier text of this contract asked the
operator first and denied the answer afterwards; version 1 (GRIP#7322) asks nothing it could not
honour, which is the same denial reached sooner, and this text now follows it.

## 4. Authority and meaning are two decisions (Astra, design sitting 2026-10-03)

"SMT passes" can establish that a meaning is structurally plausible. It can never establish that
acting on it is authorised. So the kernel keeps two floors:

- **Authority** -- deterministic and un-waivable: the invocation envelope must come from a
  trusted origin (a relayed WhatsApp or Slack prompt never reaches a binding; sender identity
  confers nothing), the selected operations must be within existing grants, and in v1 they must be
  read-only. A council cannot waive a failure here. This is the monotone binary gate the
  LLM-augmentation pattern may own.
- **Meaning** -- evidence, never a verdict: CF-IOF (Scheepers 2011, eq 2.3.6) ranks the skill
  catalogue by how uniquely the prompt's words belong to one skill's description (the MAC half);
  the SME kernel in `lib/smt/sme.py` re-ranks the top window structurally (the FAC half); an
  uncertain ranking gets one cheap, sealed pre-council pass. The result pre-fills the Koppel round.
  Meaning extraction is not a monotone binary gate, so it does not qualify for autonomous
  execution under the augmentation pattern, and v1 does not attempt it.

## 5. Why the lexicon bridge is not the extractor (measured 2026-10-03)

`lib/smt/text_structure.extract_structure` on the operator's own ~1,100-character design prompt
yielded ONE predicate, CAUSES(prompting, dogfooding). On ten skill descriptions it yielded zero
predicates for seven, and systematicity 0.0 against all ten. A floor fed by it would route every
prompt to Koppel with nothing recommended. `lib/smt/AUDIT.md` already warned that the bridge is a
lexicon, not a parser. The SME kernel itself discriminates: the design's own structural mapping
scored systematicity 6.0 (5/5 entities, 7/7 predicates including a higher-order one, consistent,
parallel-connected); a surface-only control 0.0; a two-of-six partial 2.0. So structures must come
from a reviewed, build-time JSON per skill plus a CF-IOF-ranked prompt side; only the EVALUATION
stays mechanical and Lean-backed (`formal/SMT.lean`, `formal/VERBIND.lean`).

CF-IOF's last outing (2026-10-02, operator-close detector) was KILLED on that corpus: a
word-unigram floor could not separate the classes. The regime here is different (324 skill
classes, descriptions as training text) and is NOT assumed to work: the builder measures recall@1
and the margin distribution on a blind held-out bank before the floor is allowed to recommend.

## 6. Calibration and promotion

Calibration follows KERN's method: a bank of (prompt carrying a coined verb -> intended skill)
pairs written blind by a model that never sees the floor, held out, validated across seats. The
floor may recommend a default only when recall@1 on the held-out bank is measured and the
false-recommendation rate is reported next to it.

Promotion to a skill at N=2 counts INDEPENDENT uses only -- not a retry, not a pasted copy, not
model-generated text -- and requires all three quality gates: tested (the binding's recorded
fixture replays green), monitored (its receipts show no correction or revert), ultralearned (an
/ultralearn seal exists for it). Frequency establishes demand; it does not establish correctness,
which is why the lower count buys a higher evidence bar per instance. Promotion grants no new
capability.

## 7. Harness independence

One library behind a JSON CLI: prompt and envelope in, verdict and binding out. The only
harness-specific code is the adapter that builds the envelope and performs APPLY: a
`UserPromptSubmit` hook for Claude Code (the prompt-routing hook already splits the slash command
at `lib/prompt_routing.py:105`), the hooks feature in Codex, a plugin for OpenCode. One conformance
fixture set runs through every adapter. The inbound seam is the one `docs/harness-abstraction-layer.md`
names as existing (`lib/ultracode/harness.py`); the outbound layer that document designs is not
needed for v1.

## 8. Falsifier (pre-registered)

Thirty days and at least 200 eligible novel-verb episodes; fewer is inconclusive. Target: at
least 20% less operator time per correctly completed task against ordinary prompting with matched
models and tools, no material correctness loss, and at most 2% silently wrong bindings. Theatre if
the upper 95% bound on time saved is below 10%. Counts of coined verbs, generated artefacts or
floor passes are not success metrics.

## 9. Provenance

V>> (Lourens Cornelius "Laurie" Scheepers)
___
With assistance of
  Claude Fable 5.1 (`claude-fable-5-1`, the design and the convergence loop)
  GPT-6 Astra (`gpt-6-astra` at max effort on the Codex subscription, the design sitting)
  Claude Haiku 4.5 (the gentner-enforcer agent's structural check)
Converge run `2209e354`, trajectory 29 -> 92.5 over 11 depths, genome gen 7534.
