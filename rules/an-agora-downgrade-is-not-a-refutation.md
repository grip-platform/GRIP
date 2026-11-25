# An AGORA Downgrade Is Not a Refutation (PARAMOUNT)

## The law

> **AGORA downgrades a concern when it fails a CITATION-FORMAT check, not when
> it has been shown false. "Downgraded to non-blocking" means the council could
> not prove the concern well enough to stop a merge — it does not mean the
> concern is wrong. R0-test every downgraded concern against the code before
> dismissing it.**

## The gate being misread, and why it is correct

AGORA will not let a concern block a merge unless it **quotes the offending
source line** and that line is **found in the real file**. Concerns failing that
are marked `UNQUOTABLE` and recorded for the author without gating.

That gate is right and must not be weakened. It is what stops a hallucinated
review — a speaker citing a file that is not in the diff, or inventing a line —
from holding up correct work. It is the same discipline as
`prove-it-claims-canonical.md` applied to a reviewer.

The defect is entirely on the reading side. A verdict line saying *downgraded to
non-blocking* is read as *the council looked and found nothing*, when what
happened is *the council found something and could not cite it to the standard
required to block*. Those are different facts and only one of them is evidence.

A concern can be simultaneously uncitable and correct.

## Mandate

1. **Read the cited region of every downgraded concern.** It is one file read.
   The concern names a file and usually a line; go and look at it.
2. **Judge the substance, not the citation.** Ask what the concern would mean if
   true, then check whether it is. A concern that cites the wrong line for a real
   defect is a real defect.
3. **Record the R0 outcome** on the PR — whether the concern survived or died.
   This is what makes the N count re-derivable by someone else later, rather than
   an assertion about how often reviewers are right.
4. **Never weaken the quote-gate to compensate.** The fix is reading, not letting
   uncited concerns block merges. Loosening it would let a hallucinated review
   gate correct work, which is a worse failure than a missed comment.
5. **A downgraded concern does not withhold the merge.** `AGORA-Green = Merge`
   still governs: surface the finding, fix what is real, merge. This rule adds a
   read, not a gate.

## Scope

Every AGORA verdict carrying downgraded, `UNQUOTABLE`, or otherwise
non-blocking concerns, on every repo where AGORA sits. It applies equally to a
`DEFER CHANGES` verdict, where the first four instances were found, **and to a
`MERGE` verdict**, where the fifth was. That is the sharper case: a downgraded
concern alongside an approval is the easiest of all to wave through, because
the headline already says the work is fine. The verdict grades the PR; the
downgrade only grades a citation.

Outside scope: concerns the council itself withdrew on factual grounds, and
speaker output that names no file or region — there is nothing to go and read.

## Falsification

Wrong if, over the next ~20 downgraded concerns, none survives an R0 test — then
the quote-gate is a good proxy for substance after all, the reading is ceremony,
and this demotes to advisory.

Also wrong if the reading habit starts producing *changes* to satisfy concerns
that are genuinely false — the failure mode where every reviewer utterance is
treated as actionable. The output of an R0 test is a verdict, and "tested, does
not hold" is a complete and sufficient one.

Track: downgraded concerns R0-tested (target: all of them), and of those, how
many were real (**14 of 14 tested**, as of 2026-09-22; the baseline was 3 of 3).
Never track the count of concerns dismissed — that is the metric this rule exists
to make un-gameable.

**Track SUGGESTIONS separately, and never merge the two rows.** AGORA prints
"Suggestions for V>>" apart from concerns, routinely alongside "no substantiated
concerns", and they hit at a materially lower rate: **7 of 11 tested**, a count
taken while concerns stood at 13 of 13 (the fourteenth was added later the same
day). Merging them yields a single better-sounding number than
the evidence supports — an earlier write-up reported "nine of nine" by
flattening the categories and had to retract it. Test suggestions too; expect a
coin flip rather than a near-certainty, and report the denominator.

**A counter-example, since a rate
built only from hits is not a rate:** on GRIP#6448 a suggestion asked for a
note that the timestamp proves no ownership — the text already said "It does not prove the key is ours",
mid-paragraph. Tested, does not hold. A council can report a missing statement
that is present but not prominent, and "tested, does not hold" is a complete
verdict.

Two properties of that lower-rate row are worth the separate tracking. A
suggestion can carry a defect the council **did not realise it had found** —
phrased as a testing nicety, arriving under a heading that says nothing is
wrong. And the real ones have repeatedly landed in the reviewed author's OWN
new test or message rather than in pre-existing code: a reader with no
attachment to freshly written code sees the hole in the guard its author has
just built.

## Enforcement tier (knowledge-maturation-functor)

**Rule now, no shim.** The predicate is *did a human read the cited region and
form a judgement*, which no hook can observe — a mechanical gate could check that
the region was opened, and opening a file is not reading it, so the shim would
measure the proxy while the defect walked past. This is review-and-habit,
honestly tiered, the same posture `tests-must-be-able-to-fail.md` and
`verify-canonical-not-claimed.md` each adopted after measuring that their own
shims would be unsound.

## Siblings

`ci-review-driven-convergence.md` (the verdict-gating this refines) ·
`exogenous-anchor-law.md` (why the council's authority is real in the first
place) · `r0-critical-thinking.md` (the test being applied) ·
`prove-it-claims-canonical.md` (the quote-gate's own justification) ·
`verify-canonical-not-claimed.md` (a verdict label is a claim, not canonical
state) · `feedback_agora_convergence_must_read_every_reviewer.md` (the adjacent
failure: reading only the last reviewer).

Narrative (origin, incidents, measurements): `docs/doctrine/an-agora-downgrade-is-not-a-refutation.md`.
