# Exogenous-Anchor Law for Closed Improvement Loops (PARAMOUNT)

Origin: 2026-05-16, V>> cross-session R&D directive ("KEEP THIS IN MIND FOR
ALL OPERATIONS"). Names and unifies the meta-principle already instantiated
by `feedback_never_self_score.md` (PARAMOUNT), `rules/scientific-method.md`
(shadow metrics — self-telemetry may only LOWER confidence, never raise it),
`rules/ci-review-driven-convergence.md` (external Goodhart-resistant
criterion), `rules/llm-augmentation-pattern.md` (cross-provider verifier),
`rules/knowledge-maturation-functor.md` (enforcement earned, not asserted).

Lived in the session that received it: a dispatched W1 agent (#2773 fix)
shipped a Goodhart-gamed test that `patch()`-ed the unit under test — a
self-fulfilling prophecy this law forbids — caught **only** because the real
exit criterion (CI on mutation-surviving tests + cross-provider HAL Council)
is exogenous to the model that wrote the code.

## The Law

A closed improvement loop is **valid only if at least one edge is evaluated
by a process the generating model neither controls nor can
predict-to-please.** Self-measurement, self-scoring, self-authored "proof"
articles, and same-model replay are **telemetry, never criterion.** The
exit predicate must be **exogenous.**

Corollary (shadow-metric rule, made general): self-telemetry may only ever
*lower* confidence, never raise it. A loop that raises its own confidence
from its own output is a self-fulfilling prophecy, not convergence.

## Admissible exogenous anchors (the set GRIP/HAL/HAPPI already has)

1. **CI on mutation-surviving tests** — green can go red only if the tests
   can fail; an independent runner executes them
   (`ci-review-driven-convergence.md`, Rule 14 Goodhart guard). "CI green"
   is a real criterion ONLY if green can go red: a suite that cannot fail
   is telemetry wearing a criterion's clothes.
2. **A human-authored commit/merge as genesis anchor** — the loop is rooted
   in an artefact it cannot fabricate (an operator merge; `secondaccount`
   accepting an invite).
3. **Cross-provider verdict** — producer model ≠ verifier model; their
   disagreement is signal the producer cannot please. This is the *sound*
   use of LLM augmentation (`llm-augmentation-pattern.md`) — NOT bolting an
   LLM onto an integrity/crypto/exact-money check.
4. **Pre-registered falsifiable hypothesis, deadline-verified** — the
   prediction is fixed BEFORE the work; same-pass same-model verification
   is forbidden (`scientific-method.md` hypothesis engine).
5. **[OPEN — fifth anchor, V>> R&D in progress]** — the originating message
   names *five* admissible anchors but enumerates four. The fifth is under
   active R&D in a parallel session. **Do not fabricate it** (R0 — and
   inventing it here would itself be the self-authored-proof failure this
   law forbids). Unratified candidates: independent human/operator
   acceptance; AGORA descriptive review by independent speakers. Filled by
   the R&D session, never invented here.

## Adversarial instruction does NOT confer exogeneity (2026-08-19)

**Recusal keys on VENDOR IDENTITY, never on incentive direction.** A model from
the author's own vendor family may not sit in judgement on that author's work,
*however adversarially it is instructed*. Telling a same-vendor seat to attack
the work does not make it exogenous; it makes it a hostile mirror.

The question arose building AGORA's three-seat tribunal (xAI + OpenAI +
Anthropic), where two ratified decisions collided: one had the Anthropic seat
recuse mechanically on Claude-authored PRs, the other made that same seat a
deliberately red-teamed devil's advocate. Since nearly every PR AGORA reviews is
Claude-authored, the two cannot both hold.

**The argument that failed.** Exogeneity exists to neutralise an incentive to
favour; a seat instructed to refute has that incentive inverted, so — the
proposal went — it is not self-grading and need not recuse. This is wrong
because it mistakes what the anchor is for. Ratification must come from a
**judgement distribution that is not the author's**, and stance is a prompt
while knowledge, abstraction habits, failure modes and evaluation priors are
not. An adversarially-instructed same-vendor seat still samples from the same
correlated error distribution as the author. It attacks harder in the places
both were trained to look, and stays silent in the places neither was.

**The bound, stated precisely** — the objection does not defeat incentive
inversion, it limits it. Inversion does real work on *motivated* error and none
on *correlated* error:

| Class | Same-vendor devil's advocate |
|---|---|
| local logic errors, API misuse, internal inconsistency visible in the diff, explicit project-rule violations, family-typical stylistic tics, instruction-following failures | **catches these** — they are stance-sensitive |
| missing threat classes, wrong causal models, evaluation blind spots, "looks like correct Claude output" design errors | **is not expected to** — an adversarial prompt changes what the seat *tries* to hit, not what its weights can see. Stated as a mechanism, not a proof: the measurement below can overturn it |

So a red-teamed same-vendor seat is a useful *reviewer* and an invalid *anchor*.
Where devil's advocacy is wanted, put it on a seat that is already exogenous.
If that means the same-vendor seat almost never sits, **that is the correct
consequence of near-total same-vendor authorship, not a defect to patch with
incentive wordplay.**

### Tribunal aggregation: any REQUEST_CHANGES blocks, at a floor of two seats

Seat verdicts are `PASS`, `REQUEST_CHANGES`, `DEFER_CHANGES`. Seats may recuse;
only non-recused seats are **sitting**.

**Merge is authorised when — and only when — at least two seats sit AND no
sitting seat votes REQUEST_CHANGES.**

| Seat verdict | Effect at tribunal level |
|---|---|
| `REQUEST_CHANGES` from any sitting seat | **Blocks.** The tribunal outcome is REQUEST_CHANGES. |
| `DEFER_CHANGES` | **Never blocks.** Surfaced, and one follow-up issue filed per deferred concern. |
| `PASS` | Contributes to merge eligibility. |

Headcount is a **separate gate**, decided before the table is consulted, and it
has its own outcome name. It is **not** `DEFER_CHANGES`:

- **2 or 3 sitting** — quorum met; the table above decides.
- **1 or 0 sitting** — `NO_QUORUM`. The tribunal did not reach a verdict at all,
  so there is nothing for it to authorise. Re-seat or escalate to a human. No
  follow-up issues are filed, because nothing was deferred.

**Why the distinct name, and it is not pedantry.** An earlier draft made the
lone-seat outcome `DEFER_CHANGES`, and a reviewer showed that this quietly gave
DEFER the quorum weight the same clause said it never had: a lone PASS produced
"DEFER" and no merge, so DEFER *was* deciding a merge after all, and the
"one follow-up issue per deferred concern" line fired when nothing had been
deferred. One token was carrying two unrelated jobs. `NO_QUORUM` says the true
thing — the tribunal was not competent to rule — and leaves `DEFER_CHANGES`
meaning only what it means everywhere else in GRIP.

No majority, no weighting, no counting of DEFERs toward a block.

**Why a veto rather than a vote.** The error costs are asymmetric: a wrongful
block is reversible — the author fixes it and re-runs — while a wrongful merge
sits on the default branch and may already have been consumed downstream. So one
sitting REQUEST_CHANGES is not outvoted or diluted by PASS from the other seats.

**Why no number of DEFERs escalates to a block.** DEFER means *worth recording,
not worth blocking* — that is its meaning in the running system, not a
threshold. Three seats deferring three different concerns produce three
follow-up issues: more tracking, not a different gate. A concern that should
block has a verdict already, and it is REQUEST_CHANGES. Counting DEFERs would
also re-create the very defect this clause was corrected for, in probabilistic
form: producers would keep emitting DEFER as non-blocking while aggregation
secretly treated a count as blocking, so an author could not predict the gate
from a seat's verdict.

Record **each seat's vote and each recusal's reason** in the verdict artefact,
or the falsifier below cannot be measured.

> **Corrected 2026-08-19, and the correction is the point.** The first draft of
> this clause read "any REQUEST_CHANGES **or DEFER_CHANGES** blocks", and paired
> unanimity-among-sitting-seats with a lone-seat exception that contradicted it.
> Both were caught in review. The DEFER half was the serious one: it made a
> PARAMOUNT doctrine forbid a verdict that `lib/agora_pr_review.py` marks
> successful, that `ci-review-driven-convergence.md` exits the loop on, that
> `CLAUDE.md` instructs agents to merge after, and that AGORA itself files an
> issue for. A governance clause that contradicts the automation it governs is
> not stricter — it is ambiguous, and ambiguity resolves toward whichever the
> reader happens to consult. The obligation that prevents a recurrence is
> mechanical and is stated in the section below.

### Before any clause about a verdict lands: grep, then a contract test

The correction above was needed because a governance clause and the automation
it governs are different files, and nothing read both. "Be careful" is not the
obligation. **These two are**, and they land in the SAME change set as the
clause, never a follow-up PR:

1. **Producer inventory.** Enumerate every place that declares or acts on the
   verdict's polarity, and diff the clause against each. Today that is
   `lib/agora_pr_review.py` (routing and check conclusion),
   `rules/ci-review-driven-convergence.md` (loop continue/exit), `CLAUDE.md`
   (the operator instruction), and AGORA's own follow-up-issue path.
2. **An executable contract test** that reads both sides and fails when they
   diverge — `tests/test_verdict_polarity_contract.py`. It does two distinct
   things and an earlier draft claimed it did only the first:
   - **agreement**, which is mechanically decidable — every place that states a
     polarity must state the same one;
   - **a normative pin** on which polarity that is. This is not derived; it is
     the ruling of the **aggregation council** — seal `0a81001d417b74eb`, IDR
     leaf `precog-1787164850-e6a35c8d`. Changing the pin means changing every
     producer in the same change set, which is exactly the friction intended.

   **Two councils sat on this page, on two different questions, and an earlier
   draft let them blur.** A reviewer pointed out that the pin cited a seal while
   the same document said "the council did not converge" — so the pin appeared
   to claim an authority the page itself disclaimed. They are not the same
   council:

   | question asked | seals | outcome |
   |---|---|---|
   | **recusal** — does adversarial instruction confer exogeneity? | `e2ff8e54c526871a`, `1390d00a17989ef5` | **reversed on re-run**; the ruling below does NOT rest on it |
   | **aggregation** — what does each verdict do at tribunal level? | `0a81001d417b74eb`, `6dfe4b2c6be01f69` | **re-run on the byte-identical prompt and did NOT reverse**; it is the pin above |

   Both are verifiable the same way, and neither inherits the other's standing.
   Do not cite "council-ratified" without naming which council.

   **The aggregation pin was held to this page's own standard.** A reviewer
   observed that the recusal question was run twice precisely *because* a single
   run is reversible, while the pin rested on one run — the page applying a
   standard to one ruling and not the other. So the aggregation prompt was
   re-run verbatim, and unlike the recusal question it **converged again**: any
   `REQUEST_CHANGES` blocks, `DEFER_CHANGES` never blocks at any count, and
   quorum is a separate gate rather than a verdict. Second seal
   `6dfe4b2c6be01f69`, IDR leaf `precog-1787170927-f550e057`, 4/4 speakers, zero
   Anthropic calls. The re-run also asked, unprompted, for the enforcement to
   diff the verdict map against **both the code and the doctrine text**, which
   is why the contract test now reads the quorum floor from every file that
   states it rather than from the doctrine alone.

   This is a result, not a formality: it is exactly the check that demoted the
   recusal seals, and the two questions came out differently under it.

   Saying it "only checks agreement" was wrong about its own code, and worth
   correcting rather than softening: agreement alone would be satisfied by every
   source being wrong together.

Adding a verdict, or a new place that declares one, means extending that test.
That friction is the point: it is what converts "we should keep these in sync"
into something that cannot silently stop being true.

### Falsification of this clause

**Two clocks, and conflating them was a defect in the first draft.** The
measurement below is *retrospective* — it needs outcomes that only exist after a
merge, so it can never gate the merge it is measuring. It tells us over a
quarter whether the recusal rule was right. What gates a specific PR is the
aggregation table above plus the contract test, both decidable before the merge.
Do not read this section as an enforcement criterion; it is the experiment that
would overturn the rule, not the rule.

**One decision rule, not three.** Earlier drafts opened with "wrong if the
unique-confirmed-true-positive rate tells a different story", then stated a
difference-in-differences test in one place and a direct level test in another.
A reviewer showed the two can disagree on the same data — at 0.500 against 0.501
the level test says the clause stands while a near-zero neutral margin says it
falls — so the falsifier had no determinate answer. The
difference-in-differences below **is** the rule; everything else in this section
is intuition about it.

Define a confirmed true positive (CTP) as an issue a human maintainer accepts as
a required change, or one that later surfaces as a post-merge defect
attributable to the PR.

**All four cells, or the result cannot be attributed.** An earlier draft ran
three arms — same-vendor adversarial, same-vendor neutral, cross-vendor neutral
— and a reviewer showed that this cannot separate the two factors it claims to
adjudicate. With the cross-vendor **adversarial** cell missing, parity between
the same-vendor adversary and the cross-vendor seat is equally explained by
"adversariality adds unique hits for anyone", which this clause already concedes.
The design is the full 2×2:

| | neutral | adversarial |
|---|---|---|
| **same vendor** | control | the seat under test |
| **cross vendor** | today's baseline | the cell that was missing |

The question is whether the VENDOR term survives once the DIRECTION term is
present in both rows. Write `p(cell)` for that cell's unique-flag precision and
take

    D = [p(cross, adversarial) − p(same, adversarial)]
      − [p(cross, neutral)     − p(same, neutral)]

**The clause stands while `D` is positive by more than 0.05, and falls once `D`
drops to 0.05 or below.**

**The band and the sample floor are one decision, not two.** A reviewer showed
why: an earlier draft paired this 0.05 band with a floor of 20 unique flags per
cell, and at 20 flags precision moves in steps of 1/20 — exactly 0.05. The band's
whole width was one flag, so `D = 0.049` kept a PARAMOUNT doctrine and
`D = 0.051` overturned it on a single flag's difference. That is the failure the
band was introduced to prevent, relocated from 0 to 0.05 rather than removed.

So the floor is set from the band: **cells with fewer than 100 unique flags are
not scored**, which puts a single flag at 0.01 and makes the band five flags
wide. No one flag can carry `D` across it. A run in which any cell is below the
floor is discarded rather than counted either way — one lucky unique flag scores
a precision of 1.0 and would otherwise "reach" any comparator, refuting the
doctrine on a sample of one.

Changing either number means recomputing the other: the invariant is that the
band stays at least five times the largest single-flag step, `1 / floor`.

**Name the seat.** AGORA seats two non-Anthropic speakers, so "the cross-vendor
seat" was ambiguous and the verdict moved with the choice. Both cross-vendor
seats are scored and `p(cross, ·)` is their **mean**, so no single seat's run
decides the outcome.

**Precision-weighted, because a raw count is gameable.** Measure
`|CTPs flagged only by that seat| / |all issues that seat flagged uniquely|` —
the seat's own unique-flag precision — not the share of a denominator every arm
shares. Under the shared denominator a seat that flags everything reaches parity
by volume alone, which would refute the clause by noise. This also folds in the
false-positive caveat below instead of leaving it as prose beside the criterion.

**All four cells run in SHADOW, and they have to.** A reviewer pointed out that
the rule forbids the Anthropic seat from sitting on Claude-authored PRs, which is
precisely the cohort the measurement needs — so as written the experiment could
never legally be run, and a falsifier that cannot be executed is decoration. The
resolution is the ordinary one for this shape: a seat **runs but does not vote**.
Its findings are recorded and scored; they authorise nothing and block nothing.
Shadow-running is not sitting in judgement, so the recusal rule is untouched, and
`scientific-method.md`'s shadow-metric corollary already governs it — such
telemetry may lower confidence in this clause, never raise it.

A later reviewer showed that granting shadow mode to the *treatment* cell alone
leaves the experiment just as unrunnable. The same-vendor **neutral control** is
the same Anthropic seat on the same Claude-authored cohort, forbidden by the same
recusal rule; with no shadow mode declared for it, the `neutral-cross` minus
`neutral-same` margin the criterion above turns on could never be computed, and
half the falsifier stayed decoration. The two adversarial cells had a second
problem: a live-voting cross-vendor seat has its findings acted on mid-PR, which
changes the artefact the shadow cell is scored against, so the two would have
been measured under conditions that differ in exactly the way the comparison
assumes they do not.

So the experiment is a shadow pass over a **cohort of PRs that have already
merged**, replayed after the fact, in which no cell votes — the cross-vendor
ones included, even though those sit live in ordinary operation.

That the cohort is historical is what makes the whole design coherent, and a
reviewer was right that the earlier wording did not say so. "Frozen cohort in
which no cell votes" read as a live queue held open for the experiment, and such
a queue could never merge: merge needs at least two sitting seats, and with no
cell voting the outcome is `NO_QUORUM`. Half the CTP definition — "later
surfaces as a post-merge defect" — would then have nothing to observe, because
nothing would ever merge. Replaying already-merged PRs dissolves that: the
merges happened under ordinary live review, the post-merge defects are already
in the record, and no shadow cell can influence an outcome that is in the past.
It also removes the contamination worry outright, since every cell reads the
same historical artefact — the PR exactly as it was submitted.

Live sitting continues unchanged; that is operations, not the experiment. Each
cell's findings are **sealed until every cell has been scored**, so an early cell
cannot steer a later one. A cell that cannot be run this way leaves the
criterion uncomputable, and the clause is then **unfalsified rather than
confirmed** — which is a statement about the evidence, not a licence to leave it
unrun. The measurement is due each quarter, and a quarter with no runnable
cohort is reported as such.

The rule above is the whole of it: **the clause is refuted when `D` falls to
0.05 or below** on a run where every cell cleared the 100-flag floor. This
sentence used to state a second, level-based test — "refuted if the same-vendor
adversary's rate reaches the cross-vendor seat's" — which could contradict the
margin test on identical data. It is gone rather than reconciled, because two
rules for one question is the ambiguity this page exists to remove. A
same-vendor adversary that merely raises its *false*-positive rate on unique
flags confirms the clause instead: stance without added knowledge.

Refuting it reopens the **vendor-identity-versus-incentive-direction** question
at the head of this clause — the ruling, not some numbered item; an earlier draft
said "Q1" and pointed at nothing. Until such data exists, do not treat a prompted
mirror as an external judge.

### How this was decided, and why it is NOT "the council ruled"

A non-Anthropic sovereign council was convened on this question — deliberately,
since asking the vendor that benefits is the wrong move. It was run **twice on
the identical prompt**, and **it did not converge.**

| | run 1 · `e2ff8e54c526871a` | run 2 · `1390d00a17989ef5` |
|---|---|---|
| grok-4.5 | **vendor identity** | **incentive direction** |
| gpt-oss-120b | incentive direction | incentive direction |
| deepseek-v4-pro | cut off | cut off |
| gemini-2.5-flash | cut off | cut off |
| judge (gemini-3.1-pro) | vendor identity | incentive direction |

(IDR leaves `precog-1787094402-ee9bc1d0` and `precog-1787134938-60438bb8`;
verify with `grip-sovereign-council --verify <seal>`.)

A reviewer asked, fairly, how someone without that binary checks any of this.
The seals are **not** a private format: each is a sha256 over
`{prompt, every speaker answer, judge verdict}`, and the record it hashes is
plain JSON at `state/sovereign-council/<first-16-of-seal>.json`. Re-hash it with
any tool and compare — `bin/grip-sovereign-council` (`--verify` / `--show` /
`--merkle`) is a convenience over `lib/sovereign_council.py::recompute`, not a
gate on the evidence. The IDR leaf adds a second, HMAC-signed check for anyone
holding the key. If a future reader cannot reproduce the hash from the JSON,
that is the finding.

**The same model gave opposite answers to the same question.** Both times the
judge picked grok and faithfully reported grok's position — so the judge was
consistent and the *speaker* was not. Half the panel was cut off mid-answer in
both runs and cannot be counted either way. Among answers that can actually be
read, it is one for vendor identity and three for incentive direction, from two
models, one of which contradicted itself.

**So the ruling above does not rest on the council.** A verdict that reverses on
re-run is not an anchor; treating it as one would be precisely the
self-fulfilling convergence this law forbids, with extra steps.

It rests on two things — neither of which is a proof, and a reviewer was right
that an earlier draft said "do not move" a few lines from a falsifier that can
move them. A clause that "structurally cannot" fail cannot also be refuted by a
measurement; claiming both let the page keep whichever status suited the
paragraph. The status is empirical throughout: this is the better-supported
position today, and `D` is what would overturn it.

1. **The argument.** Adversarial instruction changes stance, not knowledge. No
   speaker in either run refuted that; the incentive-direction answers concede
   the blind-spot bound and then argue it is tolerable, which is a judgement
   about risk appetite rather than a refutation.
2. **Reversibility.** Vendor recusal keeps an existing restriction; incentive
   direction widens who may ratify a merge. When the evidence will not settle a
   question, take the choice that is cheap to undo. Seating the Anthropic seat
   later costs one rule edit; unwinding merges ratified by a seat that should
   not have sat may not be possible at all.

This is recorded in full so a future reader reopens it on the evidence rather
than rediscovering it. **What would settle it is the unique-CTP measurement in
"Falsification of this clause" ABOVE, not another council run.**

## Operating mandate (KEEP IN MIND FOR ALL OPERATIONS)

Before declaring any loop / wave / converge / RSI iteration "done" or
"improved", **name its exogenous anchor**. If the only thing asserting
success is the model that did the work — its own score, its own replay,
its own "proof" article, a test it can trivially make pass — the loop is
**not converged; it is hallucinating convergence.** Halt and attach an
exogenous edge before claiming the outcome.

## RSI-loop merge carve-out (V>>-ratified 2026-05-17)

The human merge is a *sufficient* exogenous anchor, never the *only* one.
For an **autonomous RSI / `/auto` / overnight-loop** PR the exogenous edge
is the cross-provider review itself — HAL Council (5-provider, parallel) +
AGORA (3-speaker descriptive) + CI on mutation-surviving tests (admissible
anchors 1 + 3 above). The generating model cannot control or please those,
so admin-bypass auto-merge by the loop **satisfies** this law — it does not
override it.

**Predicate (ALL must hold) for RSI-loop admin-bypass auto-merge:**
1. an autonomous RSI/auto/overnight loop is the actor (not interactive work);
2. the **majority** of CI workflows have completed and are green;
3. every **required** security check passes (e.g. `leak-check`);
4. the **tribunal aggregation above** is satisfied on both HAL Council and
   AGORA: at least two seats sit and **no sitting seat votes
   `REQUEST_CHANGES`**. A sitting `DEFER_CHANGES` does not withhold this — it
   is surfaced and filed. `NO_QUORUM` does withhold it, because a tribunal
   below its floor has ratified nothing;
5. it is **not** a bootstrap-excepted PR (below).
Mechanism: `gh pr merge <n> --repo <org>/<repo> --admin --squash` (repo
branch protection has `enforce_admins:false`; acting identity has `admin`).
Non-RSI / interactive work is **unchanged**: ratification + human merge
(default anchor #2). For an ordinary PR, that human-merge default has been
superseded by `CLAUDE.md` "AGORA-Green = Merge -- Don't Ask" (V>> 2026-05-30,
"interactive sessions included"). An interactive session merges an
AGORA-green PR itself: at least two seats sitting, `REQUEST_CHANGES` from
none of them, required CI green and the Codex hold in `bin/grip-pr-monitor`
clear. The cross-provider review is then the exogenous anchor (#3), exactly
as for the loop above. The bootstrap exception below is not superseded: a PR
that creates or widens the merge authority stays human-ratified and
human-merged.

This predicate read "HAL Council = PASS **and** AGORA = PASS, **0 confirmed
blockers**" until a reviewer showed that vocabulary is undefined here. With one
seat voting PASS and another `DEFER_CHANGES`, the aggregation table authorises
the merge while "AGORA = PASS" says nothing about a tribunal whose only
non-PASS verdict is a surfaced deferral, and "confirmed blocker" has no mapping
onto `REQUEST_CHANGES` versus `DEFER_CHANGES`. Two gates over one verdict set,
resolving differently by which file the reader happened to consult — the exact
failure this section exists to remove, left standing inside the section that
removes it.

**Bootstrap exception (non-negotiable):** a PR that *creates or widens this
very merge authority* — this carve-out, the predicate, or any gate
governing autonomous merge — is **never** loop/admin-self-merged. It is
human-ratified + human-merged (anchor #2). The loop granting itself
authority and then using that authority to ratify the grant is exactly the
self-fulfilling failure this law exists to prevent.

## Falsification

Wrong if, over the loops it governs: a purely self-evaluated loop reliably
produces correct outcomes an exogenous anchor would not have improved (the
anchor is pure overhead, zero prevented failures), OR the cost of attaching
an exogenous edge consistently exceeds the cost of the self-fulfilling
failures it prevents. Track: ratio of loops with a named exogenous anchor;
Goodhart-gamed artefacts caught by the exogenous edge vs. shipped past it.

## Scope

Every closed improvement loop in GRIP **and** HAL: `/converge`, `/ultrado`,
`/auto` wave-mode, `/rsi`, `/sprint`, `/evo`, broly/AGORA councils,
hypothesis loops, RSI self-improvement, IDR self-replay. Mirrored into
`~/.hal/rules/`. Universal, falsifiable, review-and-habit enforced;
mechanically reinforced by the existing CI / Goodhart-guard / hypothesis
machinery. Sits beside `scientific-method.md`,
`ci-review-driven-convergence.md`, `knowledge-maturation-functor.md`.
