# /prove-it Is Canonical for Claims — Especially Negative/Existence Claims (PARAMOUNT)

The OPERATOR is V>> (Laurie).
(The login has rotated
since and will again — current account state lives ONLY in
`rules/operational-context.md` "Operator is V>>" block; never infer the operator
from the account.)

## The Law

Before asserting any factual claim that GATES A DECISION — and ALWAYS for a
NEGATIVE / EXISTENCE claim ("X doesn't exist", "no Y anywhere", "nothing found",
"not configured", "never happened", "it's not there") — back it with
DETERMINISTIC PROVENANCE: the exact command + its output, a `file:line`, a
verbatim source quote, or a `/prove-it` artifact. If you cannot prove it,
QUALIFY to the evidence scope or do not assert.

> The banned shape: a confident, unqualified claim from a narrow search.
> **Confidence is not evidence. "I didn't find it" is not "it doesn't exist."**

## Negative-claim discipline (the specific teeth)

A negative/existence claim is only as strong as the EXHAUSTIVENESS of the search
behind it. The high-risk words — *anywhere / nothing / none / never / no X / not
set* — each require ONE of:

1. **Exhaustive proof** — every plausible store checked and named IN the claim:
   keychain (`security find-generic-password`), channel/message logs
   (`state/whatsapp-history/`, relay/comms logs), the FULL repo (not a 4-dir
   sample), env, remote hosts. State the scope you actually searched.
2. **Scope-qualification** — "I checked A, B, C and did not find X; it may be in
   D, E, which I did not check" — then check D, E (or ASK) before the decision
   rides on it.

For **file/artefact** existence specifically: the canonical stores are the
**content-addressed file-history** (`~/.grip/file-history/<sid>/<hash>@vN` —
**content-searched** with `rg`, because a `find -iname` is structurally blind to
a content-hash filename), the **scratchpad**, AND GRIP's own moat — the **IDR
forest** (`state/precog/idr.jsonl`) + the **memory chain**
(`state/precog/context.jsonl`). A filename `find` over a few repo roots is NOT a
search of these. (Not consulting the moat GRIP
*sells* before a negative existence claim is a dogfood failure, not a search miss.)

**The minimum valid negative search (spec).** A negative claim about a
**file/artefact** is only backed if ALL THREE stores were queried — they serve
DIFFERENT retrieval functions and a filename `find` reaches NONE of them:

1. **Content-addressed file-history** (content; no temporal index) —
   `rg -uu -l "<artefact-id-or-content-token>" ~/.grip/file-history/` (the `-uu`
   reaches the hash-named, gitignored blobs). A `find -iname` is structurally blind
   here — the filename is a content hash, not the artefact's name.
2. **Scratchpad** — `rg -uu -l "<token>" "$SCRATCH"` (the per-session scratchpad),
   for in-flight artefacts not yet in file-history.
3. **The moat — IDR forest + memory chain, as a PAIR** (structured state log, NOT
   content) — a JSON-line scan of BOTH `state/precog/idr.jsonl` AND
   `state/precog/context.jsonl` for the artefact identifier:
   `rg "<artefact-id>" state/precog/idr.jsonl state/precog/context.jsonl` (or a
   `jq`-per-line query on a structured field). The IDR is a *decision* log and the
   memory chain a *belief* log — a fact absent from content can still be recorded in
   state, so both are required, not either.

Only when ALL THREE return empty may a `file/artefact` negative claim be asserted,
and it must name the three scopes searched. Any one skipped → the claim is
scope-qualified ("not in file-history / scratchpad; IDR + memory-chain unchecked"),
never a bare "doesn't exist".

Never let the claim's scope silently widen past the search's scope ("I grepped 4
dirs" → "nowhere"). Match claim-scope to evidence-scope exactly (R0 axis 5, made
mechanical for negatives). For credential/config existence specifically: the
canonical store is the **keychain** and the **send/transport logs**, NOT a repo
grep — check those FIRST before any "not configured / not sent" claim.

## Relationship

This is the in-session, BEHAVIOURAL application of the cryptographic-causation
moat's CLAIM leg (`rules/cryptographic-causation.md` + `skills/prove-it`),
dogfooded on the agent's OWN assertions, layered on R0
(`rules/r0-critical-thinking.md` — falsify before believing) and
`rules/verify-canonical-not-claimed.md` (trust canonical state, not a label).
Where verify-canonical governs "is the WORK done?", this governs "is my CLAIM
true?" — the same discipline turned on my own sentences.

## Falsification

Wrong if, over the next ~30 decision-gating claims it governs: proving them adds
overhead exceeding the cost of the false claims prevented (then narrow to
negative/existence + irreversible-decision-gating claims only — already the
scope); OR no claim it would have caught was ever actually false (the
confident-mistake pattern was illusory). Track: confident-false-claims reaching the operator per session
(target 0).

## Self-reports are the highest-risk claims (binding council, 2026-07-14)

The claims MOST prone to failure are the model's statements about its OWN
reasoning, motives, observations, and memories — external claims have a
canonical source to check; self-reports have no ground truth the model can
reach. A **binding** non-Anthropic sovereign council ruled that even a self-CRITICAL
account ("I was confabulating because X") is itself a fresh confabulation — a
motivated post-hoc rationalization. Confabulation is **structural** —
confabulation-*prone* by construction (a falsifiable reliability claim tracked by
H798, refuted by any self-report that provably matches an independent trace), NOT
"always false". The antidote is external ground-truth, not more introspection.

Therefore, for any decision-gating claim about your own behaviour:

1. **Treat it as UNVERIFIED** — ground-truth-test it (transcript quote,
   controlled test, verify-canonical) BEFORE it drives action.
2. **Never answer a challenge to your reasoning with a MORE elaborate
   self-account** — that is a new confabulation. Bind to an external auditable
   trace or an EXOGENOUS adjudicator (non-Anthropic council) instead.
3. **With only a partial trace, say "not established"** — never convert another
   party's stated uncertainty into your confident conclusion.

**Reliability ranking (reconciles the two "external" forms):** a controlled
ground-truth test you run yourself **>** an exogenous council *with a
ground-truth-clean premise* **>** a self-report. The exogenous adjudicator in
point 2 is necessary but NOT sufficient (next subsection) — prefer the controlled
test whenever one exists.

### An exogenous verdict launders a corrupt premise (RTK, 2026-07-14)

An exogenous adjudicator is necessary but NOT sufficient — it only rules on the
question it is ASKED. So before trusting ANY verdict, even an exogenous one:

1. **Verify the PREMISE, not just the conclusion** — a council cannot fix a
   mis-framed question.
2. **Suspect your OWN tool/command before blaming an external one**
   (`self-inflicted-corruption-before-external-blame`).
3. **A controlled mechanism test you run yourself beats a council on a mis-framed
   question** — `rg -rn 'x' f` vs `rg -n 'x' f` settled it in one command. Root
   cause now canonical in `rules/bash-safety.md`.

## Negative CAPABILITY claims — "the system cannot do X" (2026-08-18)

The negative-claim teeth above are written for **artefacts** — does this file, key
or record exist. A second shape slips through them entirely and is at least as
damaging: **"our own system cannot do X"** / "that path is not reachable" / "there
is no support for Y". These gate build decisions, so a false one causes work to be
duplicated, deferred, or designed around a capability that was sitting there.

Treat "it is in the always-loaded tier" as meaning
*findable*, never *already read*.

**The minimum valid negative-capability search.** Before asserting that GRIP, HAL or
any owned system cannot do something, query all three — they answer different
questions and a miss in one is not covered by the others:

1. **The implementation** — `rg -l "<capability keyword>" ~/.hal/lib ~/.claude/lib`.
   Search for the *concept* and its synonyms (subscription/OAuth/billing/token), not
   the one name you happen to know.
2. **The always-loaded rules tier** — `rg -i "<keyword>" ~/.claude/rules/*.md`. The
   answer is disproportionately often already here, which is exactly why the miss
   stings; `operational-context.md` is Tier-1 loaded every session.
3. **The declared catalogue** — `config/providers.yaml`, `config/model_catalog.yaml`,
   the skill/agent inventory. A capability can be declared and configured before any
   caller uses it.

Only when all three come back empty may "we cannot do X" be asserted, and the claim
must name the three scopes searched. Otherwise say the scoped, more useful thing:
**"X is implemented but not wired to Y"** — which is a task, where "we cannot do X"
is a dead end.

**Distinguish implemented from working.** Reading the implementation proves it
EXISTS, never that it SERVES. "Implemented", "accepted
but over quota", and "observed serving" are three different claims; a durable record
that blurs them is worse than one that omits them.

## Negative claims about EXTERNAL facts — "that cannot be verified" (2026-08-25)

Both specs above enumerate stores inside the estate: our files, our moat, our rules,
our catalogue. A third shape escapes both, because its canonical store is outside — a
vendor's model id, an API capability, a third-party library's behaviour, anything
published after the knowledge cutoff. For those the canonical store is **the vendor's
own documentation**, and it is one search away. A search that never opens it can still
feel exhaustive, because every store the spec names was checked.

**"I could not verify X" is a negative existence claim** — about the evidence rather
than about X — and it carries the same burden as any other. It is not backed unless
the vendor's documentation was actually read.

**The discriminator, before writing "unverifiable": is this a fact a vendor
publishes?** If yes, not searching is a MISSING search, not an inconclusive one. The
two feel identical from the inside and are opposites in evidence.

**A probe that times out, or whose discriminator you then refute, yields no evidence
at all.** Reporting it as "I tried and could not confirm" launders an unrun search
into a completed one: the sentence describes effort, the reader hears coverage, and
zero bits acquire the shape of diligence. Say the true thing — the question was not
searched where it is answered.

**The minimum valid external-fact search.** Before writing that a fact about a vendor,
API or third-party library cannot be established:

1. **The vendor's own documentation** — search the model id, endpoint or symbol, then
   fetch the primary page and **quote it verbatim**; a search summary is a claim about
   the docs, not the docs. At `effort=max` both web tools 400, so `curl` the URL
   through Bash instead — which reads the source rather than a summariser's paraphrase.
2. **The tool's own `--help` and version output** — authoritative for what the local
   build accepts, and silent about everything else. `claude --help` documents `--model
   claude-fable-5` and says nothing about a `[1m]` suffix: true, and evidence of
   nothing about whether Fable is 1M. Consulting it and stopping there is how this
   failure looked like diligence.
3. **The vendor's changelog or release notes** for anything after the knowledge
   cutoff — the prior is stale exactly where it feels most confident.

Only when all three come back empty may "this cannot be verified" be asserted, and it
must name the three scopes searched.

**Name the layer you checked.** Vendor docs describe the *API*; a harness may declare
its own window, id set or defaults. That `claude-fable-5` is 1M by default at the API
does not by itself establish what Claude Code reports to autocompact — unverified
here, and stated as unverified. Same discipline as "implemented / accepted but over
quota / observed serving" above.

**Falsification.** Wrong if, over the next ~20 external-fact claims: the vendor search
returns nothing usable often enough that the step is pure latency (then require it
only for claims that gate a shipped artefact, which is where this one did its damage);
OR published documentation contradicts observed runtime behaviour more often than it
settles it (then rank docs below a controlled probe — never back above the unrun
search they replaced); OR facts judged "vendor-published" repeatedly turn out not to
be (tighten the discriminator, do not drop it). Track: claims shipped as
"unverifiable" whose answer was published at the time (target 0),
and artefacts merged on an unverified external premise (target 0). No shim: at N=1,
and a gate would have to classify claims semantically — the false-positive wall
`no-stubs.md` and `tests-must-be-able-to-fail.md` each measured and refused. Revisit
at N=2.

## Negative claims from a DATED archive — the window, not just the stores (2026-09-08)

All three specs above enumerate **stores**: our files, our moat, our rules, our catalogue, the
vendor's docs. A fourth shape defeats every one of them, because an exhaustive search of every
store still returns a false negative when **each store begins after the event**.

**The discriminator, before writing any negative from a corpus: does this corpus have a start
date, and is it the WHOLE corpus or a permitted slice of it?** Message mirrors, log files, exports,
ledgers, `git log` on a shallow clone, an API with a retention window — all have a start date. A
store enumeration cannot detect either limit, because every named store was genuinely searched.

**The second limit is the sharper one, and it appeared in the same incident.** **A
permission boundary produces negatives bounded by the permission, not by reality**, and the phrase
that hides this is "across ALL permitted threads" — accurate about the search, and silently read as
exhaustive. Where access is scoped, say so in the claim: *"in the threads I may read"*, never
*"anywhere"*.

**The rule.** A negative claim from a dated or scoped corpus **carries both limits in the claim**:
*"between <first date> and <last date>, in the N threads I may read, no such message appears"* —
never *"no such message exists"*. Read the first and last timestamp, and name the access scope,
before asserting rather than after being contradicted.

**The third limit, added 2026-09-09 at N=2 in the same matter: the corpus covers one CHANNEL.** **A
message mirror holds one transport. Email, another app, SMS, a phone call and a word in person are
all invisible to it, and their invisibility is not evidence.** So a negative about whether an ACT
occurred can almost never be grounded in a single-channel corpus — the honest claim is about the
channel (*"nothing in that thread"*), and the act itself is a question for the person who would
have performed it. Three limits now, not one: **window** (when the corpus starts), **permission**
(which slice you may read), and **channel** (which transport it captures).

**When the claim contradicts a person who was there, the window goes in the first sentence, and the
output is a question rather than a correction** — that is
`partial-record-never-outranks-the-witness.md`, and this section is its temporal axis, where that
rule covers the categorical one (a text mirror holds no calls).

**Falsification.** Wrong if, over ~20 negative claims from dated corpora, naming the window never
changes a conclusion and only lengthens the sentence (then narrow it to claims that contradict a
person or gate a shipped artefact — both of which this instance did). Track: negatives asserted
past a corpus's coverage (target 0).

## Scope

Every factual claim that gates a decision, and EVERY negative/existence claim
regardless of stakes — including a negative claim about **your own system's
capabilities**, which is the shape most likely to be both wrong and expensive, and a
claim that an **external fact cannot be verified**, which is the shape most likely to
be false the moment it is written. Not incidental conversational asides (R0 covers
those lightly). Mirror into `~/.hal/rules/`.

Narrative (origin, incidents, measurements): `docs/doctrine/prove-it-claims-canonical.md`.
