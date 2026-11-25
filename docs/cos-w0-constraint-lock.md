# COS W0 — Constraint Lock (canonical output style)

**Wave:** W0 of GRIP issue #2868 · **Status:** LOCKED 2026-05-17
**Hypothesis:** H-COS-0 (H558) — every metric edge mechanically computable;
unmeasurable "intuitive" degrades to the measurable 4 (R0).
**Inputs:** issue #2868 body (authoritative, repo-tracked spec). The
broly-mesh council synthesis and the working plan are machine-local
(gitignored `drafts/`, `plans/`) — their **load-bearing findings are
inlined below (§0) so this document is self-contained**; the external
files are provenance, not a dependency a reviewer must fetch.

This wave produces **no code by design** (issue #2868 W0 row: "No
code"). It freezes the contract every later wave is checked against;
mechanical *enforcement* of that contract is W3 (the Goodhart
four-sided test) — a "no enforcement code here" observation is the
wave boundary working as specified, not a defect. Nothing below may be
widened by a later wave without amending this file in its own PR.

**Amendment record — 2026-07-31 (V>>): plain-and-fluent first.** V>>:
*"GRIP Canonical Output Style should be more /plain-language -- easier to
understand. Simpler. not laborious to read. fluent easy yet accurate
prose."* Four changes to `rules/canonical-output-style.md`, mirrored into
`output-styles/grip-canonical.md`, `hooks/caveman-session-default.py` and
`CLAUDE.md`: (1) **Layer-2 reordered** — plain-and-fluent becomes clause 1
and governs the rest; concise becomes clause 2, explicitly a trimming pass
that may never cost the reader effort; (2) **new clause 6, prose not a
form** — the GRASP parts say what a report must cover, they are not labels
to stamp on every line; (3) **caveman demoted from session default to
per-session opt-in** — the SessionStart hook no longer instructs "drop
articles/filler/hedging"; (4) **new metric edge F, Laborious.** Edge F is
the load-bearing one: §2's edges A–E punish verbosity and omission and
nothing punished over-compression, so the metric was one-sided on the
length axis and rewarded terseness without limit. That is the drift V>>
reported. A and F now bound the axis from both ends. **This widens §2**
(five edges → six) and is therefore recorded here per line 17; it does not
weaken any existing edge.

**Amendment record — 2026-07-31 (V>>, HITL-locked): terminal canonical
block.** Three clauses added to `rules/canonical-output-style.md` and
mirrored into `output-styles/grip-canonical.md`: (1) **placement** —
reasoning in the thinking block first and in full, response body = ONLY the
Layer-3 canonical block, last on screen; (2) **conditional belief** —
Layer-3 part 4 (memory-chain) included only when a rule/memory materially
drove the call, omitted otherwise (it degenerates to unverifiable
self-report as boilerplate); (3) **backward provenance refs** — each
derived claim names its derivation (`[←schema enumeration]`), the internal
anchor complementing cite.verify's external one. **This is NOT a widening
of §1 or §2.** §1 freezes what the two layers ARE and §2 freezes the
four-sided metric; neither constrains on-screen position, and (1) and (3)
are strictly additive constraints while (2) narrows an existing section's
trigger. Recorded here per line 17 regardless. Trigger: two consecutive
responses on 2026-07-31 (the `todoFeatureEnabled` turn) buried the
actionable payload mid-message with text continuing after it — V>>:
*"It's awkward to go up and read important information."*

**Amendment record — 2026-08-13 (V>>, HITL-ratified A1–A3): v3 block
shape, see-it closing, witness card.** Ratified in the /update-config
wizard (all three amendments approved; the same wizard resolved the two
operator forks — auto-fire proves but does not seal, and client-context
verdicts route to the local branded-PDF path only). Changes to
`rules/canonical-output-style.md`, mirrored into
`output-styles/grip-canonical.md`: **(A1)** when the full /witness gesture
ran, the compact witness card (box + `┆` fineprint link rows) stands as
the canonical block's closing element in place of the single see-it line —
lite `/see-it` and un-witnessed answers keep the single-line form;
**(A2)** the internal-number gloss becomes repo-qualified — "the
semantic-router fix (GRIP#4635)", never "(#4635)" — so user-scope footer
link badges can attribute references across repos; **(A3)** — **this
widens §2's edge-B reading and is therefore recorded here per line 17** —
fact-lossless is satisfied by the terminal block and its linked full
version JOINTLY (the set-difference proxy runs over block ∪ linked
artifact), under the hard condition that material caveats / UNVERIFIED
status are named in the block itself (the inline limits clause;
non-Anthropic council seal `1ba40ebcbfb44b60`, its Fatal-B objection). The
v3 body cap (/tldr + ≤8 lines, see-it closing) is the same wizard's L1
operator lock, recorded alongside. Machinery and card form:
`skills/witness/SKILL.md`. A Pool C dissent (kimi-k3) was folded before
build: the card tally speaks for cite-BOUND claims only and prints its
denominator, and the anchor row names the anchored root's manifest id —
a checkable referent, not a bare date.

**Amendment record — 2026-08-24 (V>>): bulleted session summaries, all
links inline — and the unrecorded #5018 rewrite this closes.** Two things
are recorded here, because the second is why the first needed a record at
all.

**(i) What #5018 did without a record.** PR #5018 (`8ca5c577`,
*"fix(output-style): report the session, in prose, and stop burying it"*,
merged 2026-08-24 01:08Z) rewrote `rules/canonical-output-style.md` by
**152 insertions / 386 deletions**, cutting it from **398 lines to 164**
(`git show --numstat 8ca5c577 -- rules/canonical-output-style.md`;
`git show 8ca5c577^:rules/canonical-output-style.md | wc -l` → 398). The
PR-wide diffstat is 1528 / 388 across four files; the remainder beyond the
rule file is +1376 / -2, which is `lib/session_digest.py` (+703, new) and
`tests/test_session_digest.py` (+615, new) plus `tests/test_self_report_tagger.py`
(+58 / -2) -- #5018 was a multi-topic PR that ADDED the session-digest
subsystem alongside narrowing the rule, so the large insertion count is
expected rather than anomalous (`git show --numstat 8ca5c577`). An earlier draft of this
record said "538 deletions / 150 insertions … from ~370 lines", which is
arithmetically impossible for a 370-line file and was a misreading of
`--stat`'s combined change bar (152 + 386 = 538) as a deletion count. The
AGORA council on PR #5073 caught it; a precision audit record that cannot
survive its own arithmetic protects nothing. It
installed a hard six-sentence prose cap on the session report — sound, and
grounded in a verbatim V>> rejection of a thirty-line report on 2026-08-23
plus council seal `7de6438e`. It also **deleted the v3 block shape, the
`/tldr` opener, the see-it closing row, the witness-card closing and the
edge A–F metric prose** — three of which are the HITL-ratified A1/A3
amendments recorded immediately above — with no mention of any of them in
its commit message, no amendment record here as line 17 requires, zero
reviews on the PR (`gh pr view 5018 --json reviews` → `[]`), and no update
to `output-styles/grip-canonical.md`, `CLAUDE.md` or
`hooks/caveman-session-default.py`. The mirrors were therefore instructing
the deleted contract while the rule of record said something else, from
2026-08-24 until this PR. Nothing here is a claim that the six-sentence cap
was wrong; the claim is that its collateral was never written down.

**(ii) The 2026-08-24 amendment.** V>>: *"I prefer this way of outputting
summaries... I like the bulleted point list -- it read easier. But it
should include all the links."* Reference artifact of the liked shape: the
ADR-0002 closing summary. Unanimous 3-lens broly-mesh council
`wf_c6cc6c84-b78` ruled it a GENRE-SCOPED AMENDMENT, not a default flip —
a global "final responses default to bullets" is REJECTED as the mirror
image of the form-stamping V>> banned 2026-07-31. Three changes:

- **(a) Genre routing, not a §1/§2 widening.** Bullets carry a session
  summary, close-out or multi-fact status/deliverable report (conjunctive,
  3+ parallel facts a section); prose carries causal chains, trade-off
  reasoning, RCAs and single-answer questions. What stays forbidden is a
  TEMPLATE, not a LIST — a narrative cut into `**What** — …` slots, which
  prints an empty slot when a part has nothing to say, where an inventory
  of parallel facts has no slots to leave empty. This resolves rather than
  overrides the 2026-08-23 comparison: that ruling killed the six-field
  form, this one routes the inventory to bullets.
- **(b) Body-cap re-scope — the load-bearing lock touch, recorded here per
  line 17. This SUPERSEDES the "v3 body cap (/tldr + ≤8 lines, see-it
  closing)" recorded in the 2026-08-13 amendment above.** The chain, so a
  future amendment does not trip on two live caps: 2026-08-13 locked ≤8
  LINES; #5018 replaced that with SIX SENTENCES on 2026-08-24 without
  recording the substitution (see (i)); this record ratifies the six-sentence
  cap and re-scopes it. Sentences rather than lines is itself the deliberate
  half — a line count rewards cramming several claims onto one line. The
  six-sentence cap now governs the PROSE RECAP only; a
  session-final close-out may carry a bulleted deliverable manifest below
  it that is not counted against the cap. The exemption is conditional: one
  complete fact per bullet, no filler, and narrative depth still routes to
  the linked artifact. A bullet that grows an argument counts against the
  six sentences.
- **(c) Links inline per bullet, generalising the see-it insight.** The
  one-link-per-line rule that lived in a single closing `See it:` row now
  binds every row that makes a claim: the link, path or seal sits in the
  bullet whose fact it evidences, carrying its title. The A3 limits clause
  (seal `1ba40ebcbfb44b60`) and the witness-card closing element are
  restored to `output-styles/grip-canonical.md` alongside, since (i) shows
  they were dropped without a decision.

Surfaces changed: `rules/canonical-output-style.md`,
`output-styles/grip-canonical.md`, `CLAUDE.md`,
`hooks/caveman-session-default.py`, this file, and the COS test suite.
VERIFIED NO-CHANGE (probed, not assumed): `lib/self_report_tagger.py`
(`_is_structural_non_prose:315` exempts only all-heading paragraphs and
delimiter-row tables, so bullets stay evidence-eligible — widening it would
exempt exactly the content needing evidence; `_paragraphs:217-219` splits
on a blank line, so consecutive bullets are ONE paragraph and each must
carry its own inline evidence) and `lib/bare_number_detector.py`
(`GLOSS_WINDOW = 55` at `:140`, same-line, and a bullet is a line, so the
gloss gate works per-bullet unchanged). `lib/cos_metric.py` implements
edges A–D only — there is no mechanical edge F, so the brief's edge-F
sharpening was folded into the surviving prose-not-a-form language instead
of resurrecting a taxonomy #5018 had already removed.

**Amendment record — 2026-09-11 (V>>): the block ends with a paste-ready next
step, and the manifest stops sprawling.** Two changes to
`rules/canonical-output-style.md`, mirrored into
`output-styles/grip-canonical.md`, `hooks/caveman-session-default.py`,
`CLAUDE.md`, `rules/session-protocol.md` and `skills/witness/SKILL.md`.

**(a) The terminal recommended-next-step line — a RECONCILIATION, not a new
element, and therefore not a §1/§2 widening.** A next-step element was already
specified in FOUR places and enforced in none: `CLAUDE.md` PARAMOUNT rule 5
(anti-drift), `rules/session-protocol.md` and `rules/efficiency-rules.md` rule 11
(the same four-line "What's Up Next" template, verbatim in both), and this rule's
own six-sentence recap, which already required "the single next step". A fifth
specification would have been the defect rather than the fix, so the four
collapse to one: `rules/canonical-output-style.md` is now the single definition,
the other three point at it, `rules/efficiency-rules.md`'s duplicate is deleted,
and the four-line template survives only as the post-commit expanded form in
`rules/session-protocol.md`. The always-on form is one line —
`RECOMMENDED NEXT STEP (yours) — <command or sentence>` — placed after the
manifest, after any ★ Insight block, and after a witness card. **This retires
`skills/witness/SKILL.md`'s "nothing after the block, ever" claim** (ratified
2026-08-13 as A1): the card now closes the EVIDENCE, the next-step line closes
the BLOCK. That is a narrowing of an existing ratified clause, recorded here per
line 17.

Three design points, each with a reason that is not aesthetic. The **owner tag**
(`yours` / `mine` / a named party) makes a session's outstanding operator items
greppable — on 2026-09-10 three separate things were waiting on V>> across a
dozen reports and nothing made them findable. The **command form** extends the
copy-paste-ready PARAMOUNT law (2026-09-07) to the one line the operator is most
likely to act on: when the next step is a command it is written whole, repo flag
included, so it pastes and runs. The **null case is banned outright** — `none`
and `nothing further` are forbidden phrasings — for two independent reasons: an
empty slot is the shape this rule already bans by name (`Blocked: none`), and a
line reading `nothing further` at the structurally guaranteed end of every report
is semantically the session-ending announcement
`rules/never-close-on-the-operators-behalf.md` forbids, whose metric counts
non-empty `kinds` in `logs/operator-close-hook.jsonl` against a target of zero. A
mandatory null line would poison that metric by construction; banning the
phrasing removes the question rather than adjudicating it. The line is also a
statement, never a question — "no closing offer" bans *"Want me to…?"*, not
saying what should happen.

**(b) The manifest is capped at SIX BULLETS. This TIGHTENS the 2026-08-24
exemption recorded above and is therefore recorded here per line 17.** The
2026-08-24 record made the manifest exempt from the six-sentence cap on the
condition that it stay an inventory, enforced by the exhortation "a bullet that
grows an argument counts against the six sentences". That exhortation did not
hold: the exemption became the loophole, and a six-sentence recap plus fifteen
bullets ships thirty sentences under a six-sentence cap. V>> 2026-09-10: *"and
also LESS WORDS!!"* — a repeat of `feedback_always_short_never_essays.md`
(2026-08-20). A cap is checkable where an exhortation is not. Nothing about the
2026-08-24 genre routing changes; only the manifest's size is bounded, and over
the cap the content routes behind a link exactly as narrative depth already did.

**(c) Block shape restored to the rule of record.** `## Canonical`, the `/tldr`
opener and the terminal-placement contract were absent from
`rules/canonical-output-style.md` and survived only in
`output-styles/grip-canonical.md` — the collateral of PR #5018 documented in
record (i) above. They are restored to the rule, so the preset mirrors an
authority that states them.

**Convergence on the AGORA/Codex review of PR #5485, recorded because both
findings changed the contract.** Two P2 findings survived R0 testing against the
code rather than the narrative, and both were applied before merge.

- **The marker line does not parse as shell, so "the line IS the command" was
  false.** `bash -n` exits 2 on
  `RECOMMENDED NEXT STEP (mine) — gh pr merge …`, choking on the opening
  parenthesis; the command alone exits 0. Reproduced here before accepting the
  finding. **The form is therefore TWO lines when the next step is a command:**
  the marker on its own line, the command alone beneath it. A plain sentence
  still follows an em-dash on the marker line. A line the operator must trim
  before it runs has not removed the translation step, only hidden it — which is
  the defect the 2026-09-07 copy-paste law exists to close, so shipping the
  one-line form would have violated the very law cited to justify it.
- **The next step was required twice.** The recap contract already asked the six
  sentences to carry "the single next step", so every compliant report would
  have stated its handoff in the prose and again on the terminal line —
  defeating both the consolidation and the "LESS WORDS" motive. **The recap
  contract no longer carries the next step**; the terminal line states it once.
  This narrows an existing clause and is recorded here per line 17.

**Dead code removed, verified rather than assumed.** `lib/session_digest.py:597`
read `[b for b in state.beats if b.kind == "next"]`, but `"next"` is absent from
`KINDS` at `:74` and `add_beat` rejects any kind outside it at `:301` — the
branch could never render. Deleted. VERIFIED NO-CHANGE (probed, not assumed):
`lib/cos_summary_genre.py` deliberately excludes `next` from `_REPORT_PART`
(`:49-56`, after the AGORA council on PR #5073), and the next-step line is a
plain line rather than a `-` bullet, so `_BULLET` (`:44`) never sees it — a
report carrying the line still classifies `inventory`, not `template`.

**Edge-count supersession, recorded here per line 17.** Three statements
about the metric's edge count coexisted in this file and had to be
reconciled, which is the same failure mode as the two live body caps above,
for the metric instead of the body. The 2026-07-31 record announces a
"**new metric edge F, Laborious**" that "**widens §2**"; §2 itself is titled
"The four-sided metric" and enumerates A–D; and §5 makes A–D the
falsification set. The reconciliation, verified against the code:
**edge F was never mechanised.** `lib/cos_metric.py` defines exactly
`edge_a_concise`, `edge_b_lossless`, `edge_c_smt`, `edge_d_reasoning`, and
`MetricResult.ok` is their conjunction; `tests/test_cos_metric.py` mutates
A–D and nothing else. Edge F therefore lived only as prose intent in the
rule -- which #5018 then deleted along with the rest of the edge taxonomy.

So: **§2 remains the frozen four-sided A–D contract**, the 2026-07-31 edge-F
entry is superseded as an unimplemented intent rather than a live constraint,
and (i)'s phrase "the edge A–F metric prose" refers to the deleted PROSE
naming edges A through F, never to six mechanical edges. The laborious-output
concern edge F was meant to carry is not lost: it is the trimming-pass clause
in `rules/canonical-output-style.md` ("If shorter prose takes longer to
understand, it is not concise") and the falsification clause that fails the
rule when output "makes the reader work harder for the sake of brevity".

**Amendment record -- 2026-10-05 (V>>): STE specifies plain and fluent, with a Task Card for substantive final reports.**
This amendment takes effect only when V>> merges it.
The pull request stays a DRAFT, and only V>> may merge it.
Main's amendment record of 2026-10-10 (below) changed the closer section after this record was written. The merge of 2026-10-12 keeps that text as it stands on main, so the PRIOR TEXT here describes the rule as of 2026-10-05.
The approved plan says "Gate on the premise: run H-STE-1 before T5 lands", with the falsifier "V>> waives it".

V>> gave this directive at 04:02 UTC on 2026-10-05:

> "implement ASD-STE100 as GRIP's canonical way of outputting language -- this is the upgrade of /plain-language"

V>> approved the recommendations at 04:40 UTC: "I go with your recommendations".
The operator asked for the Task Card in these words:

> "a novel, clever way of outputting one final response: 1) what was asked, 2) why, 3) for which purpose, 4) how was it implemented, 5) anything noteworthy to keep an eye on, or for which to perhaps create a follow-up task, 6) a Round-table review of implementation, 7) The next steps for yourself or for me to action"

**What widens.** ASD-STE100 specifies clause 1, "plain and fluent", for GRIP's own voice.
The structural rules apply at 100%, and the dictionary rule applies at 80%.
A descriptive sentence has at most 25 words. An instruction has at most 20 words.

Each sentence carries one instruction at most. Each paragraph has one topic and at most six sentences.
The articles stay. Technical names and quoted evidence stay verbatim.
Epistemic hedges and falsification clauses keep their meaning even when that needs another tense.

The scope covers GRIP's in-session prose, canonical block and docs.
Commit messages and PR bodies join in a second wave.

The Task Card widens the report shape for substantive final reports of shipped work.
Its headings are TASK, REASON, EFFECT, PROCEDURE, NOTES, INSPECTION and NEXT TASK, in that order.
Headings organise continuous prose. The ban covers label-value fragments and forced empty sections.
TASK + REASON + EFFECT share the six-sentence cap.

The manifest rules govern PROCEDURE and DEFERRED ITEMS. Each deferred item names its issue.
The `/tldr` line stays first, and the existing closer stays last.
An earned `★ Insight` block sits inside INSPECTION.
The `## Canonical` block still serves substantive turns that do not report shipped work.
Trivial turns stay block-free.

**What does not widen.** Rule 17, the Auto-Clarity rail, the two-layer contract and the four-sided metric stay intact.
Relayed, client and legal text keep full plain English.
The change composes with `/plain-language` and `/eli5`. It does not replace either skill.

The zero parallel transformer constraint stays intact: `lib/ste_lint.py` reads and never rewrites.
The CI ratchet checks the four voice channels and rejects added lint debt in changed rules and skills.
The turn score remains advisory and never blocks.

**Hypotheses and the merge gate.** H778 is the readability trial. Its registered claim is:

> "non-Anthropic council prefers the STE version in >= 14 of 20 paired GRIP-doc paragraphs and answers 3 factual questions per pair at least as accurately from it; falsified below 11 of 20 or on any accuracy loss"

On 2026-10-05, the first judge, DeepSeek v4 Pro, preferred STE in 16 of 20 pairs.
It answered 55 of 60 questions correctly from STE, against 58 of 60 from the original.
That half is FALSIFIED under the registered accuracy rule.
The method review found all three lost facts in the rewrite.
The STE answers were shorter than the expected answers. The original side itself lost 2 of 60.

These observations do not change the registered rule or confirm H778.
The second judge, GPT-6 Astra, preferred STE in 19 of 20 pairs.
It answered 58 of 60 questions correctly from each version.
The two halves together are FALSIFIED under the registered rule, because one judge answered less accurately from STE.
The records and both method rulings are in pull request GRIP#7508.

H779 and H780 remain named plan hypotheses, with no result asserted here.
H781 tests the operator's reports on the first five Task Cards.
Two non-Anthropic council seats predicted that the card reads worse for expert readers.

H781 requires a return to the prose mapping if 3 of 5 cards read harder.
The falsified result of H778 leaves the premise gate unmet.
Only V>> can waive that gate and merge this amendment.
A later merge does not turn the recorded H778 result into a confirmation.

**Judgement and rollback.** PROCEDURE records completed work as facts.
A numbered list can show a reproducible sequence, with an artefact anchor for each step.
The imperative applies only to actual instructions, with one instruction and at most 20 words per step.
This avoids describing a completed action as a command that still awaits execution.

Original quotations keep their punctuation even when it differs from the new prose rules.
Visible reasoning means a useful summary, within the private-reasoning limit already stated by the rule.

The PRIOR TEXT below preserves the full prior rule verbatim, including every changed paragraph.
This full snapshot avoids ambiguity at paragraph boundaries. One revert restores the old voice.
The mirrors change with the rule, so a revert must cover the whole amendment.

V>> supplied the directive and approval. GPT-6 Astra completed the resumed edit and its checks.
The earlier worker supplied the initial rule rewrite and test file. Its model identity is not verified here.

<details>
<summary>PRIOR TEXT -- rules/canonical-output-style.md before this amendment</summary>

````markdown
# Canonical Output Style (PARAMOUNT)

Origin: 2026-05-17, V>> two directives: (1) "make it more concise and
intuitively easy to understand — /plain-language and /eli5 — is my
preferred output from now on"; (2) "the ideal canonical GRIP output
style: concise + simple /plain-language; abstract/complex → simple /eli5;
less words but more intuitive meaning, token friendly (best of /caveman
reused); GRIP explains itself via inner reasoning, then final output is
the combo. This is the NEW CANONICAL style that deprecates prior
configs/playbooks/rules." GRIP issue #2868, W1. Absorbs
`rules/caveman-default.md` (now a redirect stub).

## The canonical style

For non-trivial work, show a useful reasoning summary before the answer. It must
explain the basis of the result without exposing private chain-of-thought or
inventing a first-person story of deliberation. Trivial direct questions may be
answered directly.

The final answer is the last thing on screen. Write it as a sharp colleague would
explain the work at a whiteboard: ordinary words, full sentences and one idea at
a time. Remove filler, hedging, pleasantries and repetition only after the meaning
is clear. If shorter prose takes longer to understand, it is not concise.

Keep load-bearing technical tokens exact, including paths, symbols, commands,
API names, errors, counts and commit hashes. Explain abstract ideas for a smart
novice. Use analogies only when their structural mapping is sound. Concision may
remove words, never facts.

Use prose for prose-shaped content. Use headings, bullets and tables only when
they make genuinely parallel or tabular information easier to scan. Do not turn
the report contract below into a label-per-line form.

Two genres route differently, and the split is testable. **Bullets** carry a
session summary, a close-out, or any multi-fact status or deliverable report --
content that is conjunctive, three or more parallel facts to a section, with
every link, path and seal written inline where the fact sits. **Prose** carries
causal chains, trade-off reasoning, root-cause analyses and single-answer
questions, because *because* and *therefore* have no home in a chain of
interpuncts. A narrative decomposed into labelled fragments is the forbidden
form; an inventory of parallel facts is not, and calling it one was the error
this split corrects (V>> 2026-08-24).

## Session report contract

Reporting is session-scoped, not turn-scoped. The agent maintains a persistent
session-state file through `lib/session_digest.py` and updates it as work proceeds.
The state must survive trivial questions, single lookups and verification turns:
the last small turn never erases or replaces the larger session story.

Render the accumulated state as a full report at a judged stopping point, when
the agent hands work back, or whenever the operator asks. A judged stopping point
is where the operator could reasonably leave and later need to know what happened,
what remains, or why the work took its present shape. Do not require the operator
to scroll through earlier turns to reconstruct the session.

There is an intensity gradient across the sequence. Intermediate updates stay
terse and report only what helps the operator follow or redirect the work, but
their salient decisions, evidence, results, failures and open items are captured
in session state. Terse means deferred to the final report, not discarded.

**HARD BUDGET: the narrative recap is at most SIX SENTENCES, plus one provenance
line.** Not a target, a cap. Sentences rather than lines because that recap is
prose: line counts reward cramming several claims onto one line, which reads
slower, not faster.
A thirty-line report is a failure even when every line is true, because an
operator returning cold cannot read it quickly and will skip it entirely --
which returns them to scrolling, the problem this contract exists to solve.
V>> rejected a thirty-line report on 2026-08-23 with "THIS IS STILL TOO MUCH TO
QUICKLY READ", one turn after rejecting the absence of a report at all. Both
failures are the same failure: the operator still does not know where things
stand in seconds.

The six sentences carry: what the operator asked for in their own terms, where
the work now stands, and what is genuinely waiting. They do NOT carry the next
step -- that is the terminal line's job, stated once, and a contract requiring
both would make every compliant report repeat its own handoff. A decision needed
from the operator is asked as a question inside that prose, not as a labelled
field.

**A session-final close-out may carry a bulleted deliverable manifest below that
recap, and the manifest is not counted against the six sentences** (V>>
2026-08-24: *"I like the bulleted point list -- it read easier. But it should
include all the links."*). The recap still orients a cold operator in seconds;
the manifest is the inventory they came back for, and burying its links behind a
fetch moves the scrolling problem rather than solving it. The manifest earns
that exemption only by staying an inventory: one complete fact per bullet, a
bold mini-header per section, every link, path and seal verbatim inline, and no
filler. **It is also capped at SIX BULLETS**, one line each wherever the fact
fits on one line. The 2026-08-24 wording asked for that and did not get it: an
exhortation against bullets that grow is not checkable, so the exemption became
the loophole, and a six-sentence recap plus fifteen bullets ships thirty
sentences under a six-sentence cap. Over the cap, the content goes behind a
link, which the limits clause already governs. A bullet that grows an argument
is narrative wearing a dash -- it counts against the six sentences and belongs
in the recap or in the linked artifact. Narrative depth still routes to the
linked full version, never into the block.

**Write it as PROSE, not as a form.** Three to six sentences naming the
operator's original request in their own terms and where the work now stands --
then ONE line of provenance whose every identifier carries a title. A template invites section-filling: a slot with nothing to say
still prints the slot, and six half-empty fields read slower than three full
sentences. This is not a preference. A three-sentence recap and a six-field
report were compared side by side on 2026-08-23 and the prose won on the only
axis that matters, orientation-per-second; a cross-provider council with a
non-Anthropic judge then ruled the same way (seal 7de6438e) and killed the
duplicate summary rather than keeping both.

What that rules out is a TEMPLATE, not a LIST, and the tell is which one the
labels serve. The failing shape is a narrative cut into labelled slots --
`**What** — …` / `**Why** — …` -- which makes the reader reassemble the meaning
the labels took apart, and which still prints an empty slot when a part has
nothing to say. A manifest of parallel facts has no slots to leave empty: each
bullet is one whole fact that either exists or is absent. Both rulings survive
together because they answer different questions -- the 2026-08-23 comparison
killed the six-field form, and the 2026-08-24 law routes the inventory to
bullets.

Where a decision is genuinely needed, ask it as a question inside the prose.
Where nothing is blocked, say nothing about blocking -- do not print an empty
"Blocked: none". Anything beyond this is expansion, produced ON REQUEST and
never by default.

### The block, and where it sits

The response body IS the canonical block and nothing else. The reasoning goes in
the thinking block, first and in full; the report is the LAST thing on screen,
with no prose preamble, no lead-in sentence and no trailing notes after it. V>>,
2026-07-31, HITL-locked: *"It's awkward to go up and read important
information."* Head it `## Canonical`, open with a one-line `/tldr` carrying the
load-bearing fact, and close with the recommended next step.

```
## Canonical
/tldr — one line, the load-bearing fact.

<recap: <=6 sentences of prose — what was asked, where it stands, what is
 waiting; UNVERIFIED and material caveats named HERE. NOT the next step —
 that is stated once, on the terminal line below.>

**Section** — one complete fact per bullet, its link/path/seal inline,
carrying a title. At most six bullets.

`★ Insight ─────…`              (only when earned; outside the recap
                                 budget, inside the block)

RECOMMENDED NEXT STEP (yours)
gh pr merge 5474 -R grip-platform/GRIP --admin --squash
```

That shape was deleted from this file on 2026-08-24 by PR #5018 (`8ca5c577`, the
six-sentence-cap rewrite) with no amendment record, while
`output-styles/grip-canonical.md` went on instructing it -- so the preset and the
rule of record disagreed for a fortnight. It is restored here, where the single
definition belongs.

### The recommended next step (the block's last line)

Every canonical block ends with one line naming what should happen next and whose
move it is:

```
RECOMMENDED NEXT STEP (yours)
gh pr merge 5474 -R grip-platform/GRIP --admin --squash
```

```
RECOMMENDED NEXT STEP (mine) — wire the freshness probe into discover()
```

**A command sits ALONE on the line below the marker; a plain sentence follows
the em-dash on the marker line itself.** The two-line form is not decoration and
was not the first draft: that draft put the command after the em-dash, claiming
the whole line "IS the command", and Codex refuted it on this PR with a runnable
test -- `bash -n` exits 2 on `RECOMMENDED NEXT STEP (mine) — gh pr merge …`,
choking on the opening parenthesis, while the command alone exits 0. A line the
operator must trim before it runs has not removed the translation step, it has
hidden it, which is precisely the defect the copy-paste law exists to close. The
marker stays greppable either way, because it is still a fixed string at the
start of its own line.

It sits after everything else: after the manifest, after any ★ Insight block, and
after a witness card when the full gesture ran. The card closes the EVIDENCE; this
line closes the BLOCK.

**The owner comes first, in parentheses** -- `yours`, `mine`, or a named party.
That is what makes a session's outstanding operator items findable. On 2026-09-10
three separate things were waiting on V>> across a dozen reports and nothing made
them greppable; a fixed marker plus a machine-readable owner is one `rg` away.

**When the next step is a command, that command occupies a line of its own** --
verbatim and complete, repo flag included, nothing elided, no `▎` gutter, and
nothing else on the line. This is the copy-paste-ready law below applied to the
step the operator is most likely to act on: select the line, paste it, and it
runs, with no translation from advice into action and no trimming first. When
the next step is not a command it is a plain sentence on the marker line, and it
names its owner either way.

**The gloss goes in the recap, not on the command.** This rule's reference clause
requires every internal number to carry a plain-language gloss on every mention,
and the copy-paste law requires the command to run unedited -- appending "(the
AGORA grounding fix)" after the command would be copied along with it and break
the paste, exactly as the owner tag would if it shared the line. The two are reconciled by placement, not by exception: the recap prose
already names what the work is, so the command line stays pure and the gloss sits
one sentence above it. A next-step line whose number appears nowhere in the recap
is a bare number and fails the reference clause; fix the recap, never the command.

**It is never null.** `none` and `nothing further` are forbidden phrasings, for
two independent reasons. First, an empty slot is the exact shape this contract
already bans by name -- the same defect as printing `Blocked: none`. Second, a
line reading `nothing further`, structurally guaranteed to be the last line of
every report, is semantically the session-ending announcement that
`rules/never-close-on-the-operators-behalf.md` forbids; that rule is measured by
counting non-empty `kinds` in `logs/operator-close-hook.jsonl` against a target of
zero, so a mandatory null line would poison its metric by construction. Banning
the phrasing removes the question. If nothing is on the agent, the line names
whose move it is instead.

**It is a statement, never a question.** The no-closing-offer discipline bans
*"Want me to…?"*; it does not ban saying what should happen. Where a decision is
genuinely the operator's, the question is asked inside the recap prose as this
contract already requires, and the next-step line still states the move.

Turns that ship nothing substantive carry no block, so they carry no line.

This element supersedes the four-line "What's Up Next" template, which had been
specified in four places and enforced in none. That block survives only as the
post-commit expanded form in `rules/session-protocol.md`; the one-liner here is
the always-on form, and this rule is its single definition.

The six parts below are what a report must COVER when expanded, not sections to
stamp on every response. A part with nothing load-bearing to say is omitted, not
filled. When the budget and the six parts conflict, the BUDGET wins: a part
with no room is a part omitted, never a part abbreviated into noise.

On expansion, cover these in clear, flowing prose:

1. What was achieved, including incomplete or failed work that changes the state
   of the session.
2. Why the work mattered: the operator goal or trigger it served.
3. The operator's request and intent, and the agent's intended response. Quote the
   original request from its source; do not paraphrase a memory of it. Relate both
   intents explicitly to what actually happened, and mark any inferred intent as
   an inference.
4. How the result was reached, as an artefact-anchored decision record: the chosen
   path, material rejected alternatives, and any signed IDR leaf or chain reference.
5. What the agent believed, but only when a specific rule, memory or fact materially
   drove a decision. Name and anchor that source, and cite the memory-chain node when
   one exists. Omit this part when no such belief changed the call.
6. Deterministic provenance for every decision-gating claim through `cite.verify`:
   place the supporting command and output, `file:line`, verbatim quote or explorer
   URL beside the claim. Outward, team, client and legal deliverables receive the
   full `/prove-it` receipt.

Parts 4 and 5 are evidence contracts, not invitations to narrate deliberation. A
first-person account such as “I considered X and rejected Y” is unverifiable
self-report and can trigger the confabulation Stop-hook. Anchor the decision to the
artefact that embodies the chosen path and, where possible, proves the alternative
absent. If no artefact supports a rationale or belief, mark it **UNVERIFIED** rather
than asserting it as fact. This lesson came from the report contract's own first
use and must not be compressed away.

An unrecorded consequential decision is a gap to close, never to hide. A claim
GRIP cannot back is either omitted as a claim or listed explicitly as **UNVERIFIED**.
Evidence belongs beside what it supports, not in a detached ledger.

## References and numbers

Name what every operator-facing number, link and identifier is. Never assume the
reader remembers a mapping.

This binds LINKS and SEALS exactly as it binds numbers, which is where it was
first broken: a bare "merge #5014?" and a bare "verify: seal 8fd0825b" were both
shipped inside a report otherwise built to be scannable. V>> rejected them on
2026-08-23 -- "a link without a reference to the work done in the session is not
signal". A URL the operator must open to discover what it is has moved the
scrolling problem rather than solved it. Carry the title:
"#5014 -- /grip-watch, find where to watch anything live", not "#5014"; and
"seal 8fd0825b (the output-style diagnosis)", not a naked hash. Keep the exact number for auditability and pair every mention with a
plain-language functional gloss: “the semantic-router fix (GRIP#4635)”, not bare
“#4635”; “the five failing tests”, not bare “5”. Repo-scoped issues and pull
requests are repo-qualified. Quoted evidence and code are exempt because their
exact text must remain intact.

Inside a bulleted manifest this binds per bullet: the link, path or seal sits in
the bullet whose fact it evidences, carrying its title, not gathered into a
trailing block of bare URLs. A reader scanning one bullet must not have to look
elsewhere for its evidence. This is the same insight the older one-link-per-line
closing row encoded, generalised from a single closing row to every row that
makes a claim.

Backward reasoning references must name the derivation in plain words. Do not use
bare or unstable step numbers. Prefer “found by enumerating the schema” to bracket
notation unless the compact notation genuinely makes a non-obvious origin clearer.

## Copy-paste-ready output (PARAMOUNT)

Origin: 2026-09-07, V>>. A drafted WhatsApp message was rendered in a markdown
blockquote. Claude Code paints blockquotes with a `▎` gutter, and that character
is copied along with the text — so the operator pasted a message into WhatsApp
carrying rendering artefacts he then had to strip by hand. He had already built
`/copy-cc-clean` to clean up after exactly this, which means the defect had been
recurring long enough to earn its own tool. His instruction: *"never display
messages in a way that is not ready for copying and pasting from the terminal.
That's the reason why I created the copy-cc-clean command, in the first place.
Fix it via infrastructure and core learning kernel."*

**The law.** Anything the operator will copy VERBATIM to another surface — a
message to send, a command to run, a block of text destined for WhatsApp, Slack,
email, or a document — is rendered in a **fenced code block**, never a blockquote
and never with markdown emphasis the destination does not support.

- **Blockquote `>` is forbidden for sendable text.** It renders a `▎` gutter that
  copies with the content. Use it only for quoting something the operator will
  READ, never for something he will SEND.
- **Match the destination's markup, not markdown's.** WhatsApp uses `*bold*`,
  `_italic_`, `~strike~` and triple-backtick monospace; it has no `>` blockquote
  and no `**bold**`. Markdown `*italic*` renders as literal asterisks there.
  Plain text with real paragraph breaks is the safe default.
- **Put it on the clipboard when the artefact is single and unambiguous.**
  `pbcopy` costs one call and removes the copy step entirely. Say that you did.
- **The test:** could the operator select the block, copy it, and send it with no
  edit at all? If any character would have to be removed first, the rendering is
  wrong — not the operator's clipboard.
- **The block's last line is inside this law.** When the recommended next step is
  a command, it is written out whole — repo flag, full path, no elision — so the
  one line the operator is most likely to act on pastes and runs. Advice they
  must first translate into a command has moved the work rather than done it.

Falsification: wrong if, over the next ~20 drafted messages, fenced blocks prove
to copy no more cleanly than blockquotes in the operator's terminal, or if
`/copy-cc-clean` invocations do not fall. Track: operator-run clean-ups after a
drafted message (target 0; the tool's existence says the prior rate was not 0).

## Non-negotiable rails

Rule 17 wins on substance. This style governs in-session working and explanatory
prose; it never compresses client or legal content, relays, commit messages, pull
request bodies or shipped documentation. Those remain full plain English.

Pause compression for security warnings, irreversible-action confirmations,
multi-step instructions where ambiguity risks harm, and whenever the operator is
confused or repeats a question. Resume only after the critical section.

`stop caveman` or `normal mode` restores full prose for the session.
`/caveman lite|ultra|wenyan` changes compression intensity; it does not replace
this voice or weaken the report and safety contracts.

## Falsification and revert

This rule is failing if output drops facts, hides session history behind a trivial
last turn, reports unanchored deliberation as fact, emits unexplained numbers, or
makes the reader work harder for the sake of brevity. It is also failing if
compression causes a safety misread or reaches a Rule 17 surface. Correct the
narrow failure first; if the style repeatedly lowers accuracy or clarity, restore
the redirect stubs and the prior indexed rule. Per session, use `normal mode`.

The bullet routing specifically is failing if bullets spread beyond the summary
genre into causal explanation, if a manifest grows into a bullet-essay rather
than one fact per line, or if bullets decay into telegram fragments -- the
dropped articles the trimming pass already bans, now hiding behind a dash. Any
of those means the split has widened past what V>> asked for on 2026-08-24:
narrow it back to the inventory, do not drop the routing.

The recommended-next-step line is failing if, over the next ~20 substantive
reports, the operator never acts on one, or the owner tag never once helps him
find what is waiting on him -- then it is ceremony and should be demoted to
session-final blocks only. It is also failing if the line's command form proves
to need editing before it runs as often as it pastes clean, which would mean the
copy-paste law is being satisfied in letter rather than in fact. Track: reports
ending without the line (target 0), and `(yours)` items the operator had to hunt
for anyway. Do not track how often the line is emitted -- it is mandatory, so
that number measures nothing.

The six-bullet manifest cap is failing if it pushes real inventory behind links
the operator then has to open, so that a close-out reads shorter and informs
less. The fix there is a second section under its own mini-header, not a wider
cap: the cap exists because the 2026-08-24 exhortation against argument-bullets
demonstrably did not hold, and widening it restores the loophole rather than the
information.
````

</details>

**Amendment record -- 2026-10-10 (V>> operator correction): who owns the next
step.** V>>: *"you say the next step is yours, but it's something you could have
done automatically. Why are there so many recommended-next-step halting
conditions, asking me to do things like run bash commands myself, or
instructions to approve things that should be happening automatically."* The
trigger closer was "tell me to watch PR #7836 and I'll take it through review and
merge", for a PR the repo's merge sweep had already merged. The cause was in this
contract: the 2026-09-11 record above wrote the always-on form with `(yours)` as
its owner, the rule's only `(yours)` worked example was a bare merge command
(`gh pr merge 5474 …`) with no class named beside it -- GRIP#5474 changed AGORA's
own grounding guard, so that merge was genuinely his (an Authority step), but
nothing said why, and it read as the shape of every closer -- the null ban left
"name the operator" as the only legal ending once the agent was done, and
`rules/canonical-output-closer.md` framed every closer as his yes-or-no.

Changes to `rules/canonical-output-style.md`, mirrored into
`output-styles/grip-canonical.md`, `hooks/caveman-session-default.py`,
`CLAUDE.md` (PARAMOUNT rule 5 and the closer pointer),
`rules/session-protocol.md`, `rules/efficiency-rules.md`,
`rules/canonical-output-closer.md`, `skills/hitl/SKILL.md` and
`skills/clarify/SKILL.md`: (1) **the owner test** -- `(yours)` only for the four
HALT classes (irreversible, including any production deploy or change; fork,
defined as in `lib/pending_materiality.py` with no "evidence settles it" escape;
information gap; commercial) plus the standing carve-outs for authority
(bootstrap-excepted PRs, which stay `(yours)` even when AGORA passes them;
operator-reserved switches; a git-crypt diff only a human can read) and danger (security, secret or guardrail regressions);
everything else is `(mine)` and done in the same turn; something automatic the
agent is driving stays `(mine)`, and only a party outside the loop is named as
the owner; doubt resolves to `(yours)` once a read-only check the agent can run
itself (the command's source or `--help`, a dry run, origin) has failed to
settle it, and doubt means a fact about the step itself, never an older ask line that `CLAUDE.md` already supersedes (the
interactive "human merge" line in `rules/exogenous-anchor-law.md`, the
interactive "V>>-gated" merge in `skills/finish/SKILL.md` Step 7 (its own text
reconciled in GRIP#7857), and the commit and working-branch push asks in
`.claude/CLAUDE.md` rule 5 and `skills/asking-users/SKILL.md`, all named, each
superseded only where `CLAUDE.md` itself says so; any other check-in those files
call for, the three-step checkpoint included, stands and is asked mid-task the
`/hitl` way, never parked as a `(yours)` closer); (2)
**the worked examples replaced** -- two `(mine)` steps (one of them the
2026-10-10 PR case done right), a named outside party, the browser step of a
sign-in the agent has already started (the smallest slice of an information gap)
and a commercial decision; the template's closer is now `(mine)`; (3) **the null
case reworded** to end on the work instead of handing the move to him; (4)
**statement-form offers** ("tell me to X and I'll do it") named as the banned
closing offer. The always-on form recorded in the 2026-09-11 entry now reads
`RECOMMENDED NEXT STEP (<owner>)`; that entry is left as written, since a record
is history.

**This narrows, it does not widen.** §1 and §2 are untouched. The owner test
removes nothing from what already waited for the operator: the HALT classes, the
bootstrap exception in `rules/exogenous-anchor-law.md`, a merge-machinery PR that
`bin/grip-pr-monitor` holds and whose effect changes merge decisions, a merge
whose deploy cannot be undone (the routine per-merge self-deploy of `CLAUDE.md`
"Commit Often" belongs to its authorised merge), every check-in in
`.claude/CLAUDE.md` rule 5 and `skills/asking-users` that `CLAUDE.md` does not
itself supersede, and the list in
`rules/autonomous-blocker-resolution.md` all stay `(yours)` -- its "prod"
example and its secret and guardrail regressions are written into the
Irreversible and Danger classes on the rule, the preset and the SessionStart
directive (`CLAUDE.md` rule 5 names the classes without those examples) -- and no merge
authority changes; `rules/exogenous-anchor-law.md` is not edited. Two small
mechanical anchors ride along: `lib/pending_materiality.py` no longer certifies
"tell me to …" or "let me know if …" as an information gap (they fall to
"unknown", which still keeps the question), and `lib/next_step_router.py` reads
the operator's ` -- ` separator whole. `tests/test_canonical_closer_owner.py`
pins the examples, the rule and preset template as identical, the hook's copy,
the production, secret and guardrail examples, the "not doubt" clause and the
settle-first doubt clause on all three surfaces, the residual wording ("anything
else") wherever a surface gives agent-doable work to the agent, and the
Authority item's bootstrap qualifier. Recorded here per line 17.

## 0. Inlined council findings (self-contained — provenance §7)

The LANDED broly-mesh council (6 deliberators, every verdict cites real
file:line + HOLISTIC `d934803e`; 15 files audited) is summarised here so
W0 stands alone. Verdict: **REFACTOR YES but narrow** — KEEP 7 ·
REFACTOR 3 (GRIP `CLAUDE.md`, GRIP `AGENTS.md`, `rules/CLAUDE.md` 1-row)
· CREATE 2 (HAL root `CLAUDE.md`+`AGENTS.md`) · REDIRECT-STUB 2 (HAL
`agents/`+`modes/` `CLAUDE.md`, byte-identical to GRIP) ·
KEEP+scope-correction 1 (`happi.md`).

- **CC#1 (highest value):** GRIP's name + capability counts contradict
  across its own two root docs (`CLAUDE.md:11` "Reasoning & Intelligence
  Platform" vs `AGENTS.md:8` "Recursive Improvement Protocol"; 3 stale
  count triples). Fix = **de-hardcode**, NOT re-number (re-numbering
  re-rots — correct-by-construction).
- **CC#4 (corrects the plan):** `happi.md` is a normative 5-parser
  wire-protocol spec with zero voice section by design — a voice section
  is a category error. W4/W5 get a **one-line frontmatter pointer only**.
- R0 rejected a blanket dir-level refactor (cosmetic churn = JIT
  violation) and rejected force-merging the two `skills/CLAUDE.md`
  (genuine per-repo divergence, not a DRY target).

## 1. The two-layer contract (frozen)

**Layer 1 — Visible reasoning (non-optional).**
`settings.json "showThinkingSummaries": true` is already on. The reasoning
summary IS part of canonical output, not a debug aside. A canonical
response, when the task is non-trivial, shows *how* the conclusion was
reached before the conclusion. Falsification edge D below makes
"reasoning omitted when complexity warrants" mechanically detectable.

**Layer 2 — The final answer, one combined style.** Ordered, each clause
testable:

1. **Concise** — caveman token discipline as the *substrate*: drop
   articles / filler / hedging / pleasantries / trailing restatement.
   Every technical token verbatim: file paths, symbols, numbers,
   commands, API names, PR/issue IDs, identifiers.
2. **Simple / plain** — `/plain-language`: no insider jargon unexplained;
   a first-time reader understands without asking.
3. **ELI5 for the abstract** — when a concept is abstract/complex,
   `/eli5`: explain to a smart novice via intuition.
4. **SMT-coherent mappings** — any analogy is a sound *structural*
   mapping (Gentner Structure-Mapping: systematic, one-to-one,
   relational not surface). Reuse proven mappings; never decorative
   surface analogy. Enforced by the existing `gentner-enforcer` agent.
5. **Lossless** — concision removes words, never facts.

Net contract: fewer tokens than today's prose, more intuition than
today's caveman, plus a reasoning trace neither currently shows.

## 2. The four-sided metric (frozen — Goodhart-proof)

A canonical-style output is REJECTED if any single edge fails. Each edge
is mechanically computable on a held-out sample (H-COS-0). W3 builds one
fixture per edge; each fixture must flip red under exactly its mutation
and the good exemplar must stay green on all four.

| Edge | Failure condition | Mechanical proxy (computable) |
|---|---|---|
| A — Verbose | Output padded with filler / preamble / postamble / hedging | Preamble/postamble regex hit (`concise-communication` skill banned-phrase set) OR token count > baseline ceiling for the same answer content |
| B — Fact dropped | A technical token present in the source answer is absent from the styled answer | Set difference of extracted technical tokens (paths/symbols/numbers/commands/API/IDs) source vs styled is non-empty |
| C — Surface analogy | An analogy is decorative / surface-similarity, not a sound structural mapping | `gentner-enforcer` verdict = incoherent on any analogy span |
| D — Reasoning omitted | Task is non-trivial yet no reasoning trace precedes the answer | **Non-trivial = ANY of:** (i) task requires ≥2 distinct steps/tool-calls to answer; (ii) ≥2 technical tokens from different domains co-occur in the task; (iii) the task asks "why" / a trade-off / a decision (not a single direct command/lookup). If non-trivial AND no reasoning-summary segment precedes the answer → edge D fails. Pure direct commands ("commit this", "read X") are trivial → edge D not asserted (mirrors the 6 GRIP-First exemptions). |

**R0 degradation clause (binds W0):** "intuitive" and "SMT-coherent" are
not directly numeric. If, on the W6 held-out sample, an edge cannot be
computed, that edge degrades to the nearest measurable proxy in the table
above and the unmeasurable framing is dropped — never asserted. The
metric is the 4 measurable edges, not a feeling.

## 3. Verified deprecation inventory (frozen — each with migration note)

Filesystem-verified against origin/main 2026-05-17 (commit `3313870e`):
`rules/canonical-output-style.md` ABSENT (W1 creates it);
`rules/caveman-default.md` PRESENT (W1 stubs it);
`~/.hal/CLAUDE.md` + `~/.hal/AGENTS.md` ABSENT (W4 creates them).

| # | Artefact | Disposition | Migration note (reversible, no silent loss) |
|---|---|---|---|
| 1 | `rules/caveman-default.md` | **Absorb** | Token technique → Layer-2 substrate of W1 rule. Replace body with a ≤5-line redirect stub pointing at `rules/canonical-output-style.md`. Per-session knobs `stop caveman` / `/caveman lite\|ultra\|wenyan` PRESERVED as intensity controls of the unified style (skill `skills/caveman` unchanged). Reversible: restore stub body. |
| 2 | `settings.json "outputStyle"` | **Supersede → ENFORCE (amended W7, #2868)** | W0 line 17 reserved amendment-in-its-own-PR; this is that PR. The original "no settings.json edit (avoids install.sh churn)" was a cost heuristic, never a correctness claim — REVERSED on V>>'s 2026-05-19 falsifying lived evidence (reasoning trace invisible, output verbose: rule-only enforcement is behaviourally inert; the W2 `lib/canonical_output_style.py` shim was orphaned/never invoked — origin/main-verified, and was DELETED in #2868 ultraenhance per issue #2923). W7 delivers harness-natively: `output-styles/grip-canonical.md` (Lever B) + `outputStyle`/`showThinkingSummaries` GRIP-managed (`lib/settings_merge.py`) + set in this reference + `output-styles` added to `scripts/install.sh` CORE_DIRS. One-time additive churn ≪ a permanently failing canonical style. Reversible per W1 revert clause + the `972f01fe9`→`12f7a80c6` output-styles precedent (restore this row, drop the 2 keys + array member, delete the preset). |
| 3 | Rule 17 (Plain Language, PARAMOUNT) | **Subsume + strengthen** | Stays PARAMOUNT on substance. W1 rule adds concision + intuition-first + SMT-soundness + visible reasoning ON TOP. Rule 17's plain-language guarantee AND the Auto-Clarity safety rail remain non-overridable (verbatim in W1). No deletion. |
| 4 | GRIP#2776 eli5 ladder (#1878) · `/eli5` · `/plain-language` | **Compose, not delete** | They become the building blocks the W2 default transform calls. Ad-hoc manual invocation replaced by the always-on default. Skills/commands untouched. |
| 5 | Any mode/playbook with a divergent explanatory style | **Reconcile → redirect stub** | One-line redirect to the canonical rule (the `rsi-optimal→auto` precedent). Never a hard delete. Discovered during W5 re-audit; none KEEP-verdict file is touched. |
| 6 | `rules/CLAUDE.md` index row for `caveman-default.md` | **Repoint (1 row)** | Gated on W1 landing. The row points at `canonical-output-style.md`; caveman-default.md keeps its stub body so the redirect never dangles. |

**Deprecation discipline (KISS, reversible):** every superseded artefact
gets a one-line redirect stub, never a hard delete. Reversible by
restoring the stub's body.

## 4. Hard constraints carried forward (non-negotiable)

- **Rule 17 substance + Auto-Clarity rails win.** Security warnings,
  irreversible-action confirmations, multi-step safety sequences → full
  prose, never compressed (verbatim from `caveman-default.md`).
- **Channel/client/legal/Slack-relay surfaces (Rule 17 user-facing) keep
  full plain English.** The unified style governs assistant
  working/explanatory output only.
- **Compose, never rebuild (Rule 21).** Reuse `/plain-language`,
  `/eli5`, the #1878 ladder, the caveman token-pass, `gentner-enforcer`.
  Zero parallel transformer (H-COS-2).
- **Goodhart four-sided (Rule 14).** Section 2 is the contract; W3 is its
  test; each mutation must flip exactly its edge.
- **Council R0 boundaries honoured.** Do NOT refactor KEEP-verdict files
  (cosmetic churn = JIT violation). Do NOT force-merge the two
  `skills/CLAUDE.md` (genuine per-repo divergence). happi.md gets a
  ONE-LINE frontmatter pointer ONLY — never a voice section (CC#4
  category-error correction). De-hardcode GRIP name/counts (CC#1) —
  never re-number.

## 5. W0 falsification

H-COS-0 is wrong if, on the W6 held-out 2026-05-17 PR-status sample, any
of edges A–D cannot be computed at all (not merely degraded) — then the
four-sided metric is not mechanically enforceable and the contract must
be rebuilt around whatever IS computable. Tracked at W6.

## 6. Wave order locked (precondition chain)

W1 (rule) MUST ship before any pointer wave — pointers must not dangle
(council correct-by-construction precondition). Then W2 → W3 → W4 → W5 →
W6 per issue #2868. W5 applies the LANDED council synthesis in its
sequenced order; W6 executes the deprecation cutover (stubs in) only
after the per-wave PRs converge.

## 7. Provenance (not a dependency — traceability)

The authoritative, repo-tracked source for this contract is **GitHub
issue #2868's body** (self-contained spec, reviewable without this
repo). Two machine-local artefacts informed it and are recorded here for
audit only — their load-bearing content is already inlined (§0), so this
document does NOT depend on fetching them:

- `drafts/broly-claude-agents-md-refactor-synthesis-2026-05-17.md` —
  the LANDED 6-deliberator council synthesis (gitignored `drafts/`;
  every verdict cites real file:line + HOLISTIC `d934803e`).
- `plans/concise-intuitive-eli5-default-output.md` — the working plan
  v2.1 (gitignored `plans/`).

Filesystem inventory in §3 was verified against origin/main commit
`3313870e` (2026-05-17): `rules/canonical-output-style.md` ABSENT (W1
creates), `rules/caveman-default.md` PRESENT (W1 stubs), HAL root docs
ABSENT (W4 creates). A rebase that invalidates this is caught at W5/W6
re-audit (each wave re-verifies its own targets via `git show
origin/main:<path>` per `verify-canonical-not-claimed`), not by a CI
guard for this one-shot 7-wave loop (a bespoke CI check here would be
YAGNI / the over-engineering the council itself warns against).
