# Reasoning Lenses: Anything May Propose, Only What Survives Attack May Decide (PARAMOUNT, subordinate to R0)

Origin: 2026-10-03. V>> asked for how an intelligent five-year-old would see a problem, Occam's Razor, common sense and gut feel, and said "GRIP should have these capabilities". The first version turned them into "cheap signals" to distrust, and then counted words. V>> retired that on 2026-11-01. GRIP keeps the ways of finding answers, and it attacks every output.

## The law

> **Anything may propose. Only what survives attack may decide. Evidence always wins.**

This is Popper's split. Where a conjecture comes from does not matter, and only severe tests count. So a simple answer, a child's question and a hunch are all welcome, and none of them is a verdict.

## Ways of finding answers

`/why-not-just` runs the first. Occam and gut have no command. Use them when an adult plan, a long debug or a growing design has become the default.

**Why not just?** Say the want in a five-year-old's words, and keep what would prove it wrong. For each requirement, ask "why do we need this?" until it has evidence or has none. Ask what can be removed, because people default to adding and miss removal unless prompted (Adams et al. 2021).

Copy the missing habit, never "child genius". Children are far weaker at inventing tools than at copying them (Beck et al. 2011). The simple answer stands until a test rejects it, and complexity earns itself with a failing test.

**Occam.** Test the boring causes first: your own command, a stale state, the wrong branch, the wrong flag. List what each candidate needs to be true that nobody has observed. Run the cheapest test that tells the candidates apart, with a control (`anchor-on-the-artefact-not-the-session.md`). Among candidates that pass the same tests, take the fewest additions. Simple never outranks a passing test.

**Gut.** The operator's gut is a directive to investigate, and it is never silently overridden. The model's gut is a pattern-match: say it, then test it. A model's account of its own reasoning is a fresh guess (`prove-it-claims-canonical.md`), so a model's gut is measured from outside or not at all.

## The attack, by hand

When a claim gates a decision, climb this ladder and stop at the first kill. Set a cap on seat calls for the run, and stop at the cap.

1. **Count first.** Run a cheap test against a control. A controlled test outranks any council.
2. **Tombstone.** State how the claim can die, and register it with `lib.hypothesis_engine.register`. A claim with nothing to test is UNTESTABLE, and it decides nothing.
3. **Naive prior.** Ask what a sensible outsider would expect. This is where common sense lives. It sets the burden of proof, and it proves nothing itself: the bigger the surprise, the stronger the evidence must be.
4. **Analogy check,** when an analogy carries weight. Write predicates, run `gmap` and `candidate_inferences` from `lib/smt/sme.py`, and test each projection as a new prediction.
5. **Red team.** Outside seats attack with the `/red-teaming-strategic-decisions` vectors, and an outside judge seals the result (`lib/sovereign_council.py`). A seat with no evidence beyond the claim's text proposes a test and cannot kill (H904). Ask the `/falsify-self` anti-stratagem question: does this test really test the claim?
6. **`/broly`,** only for an irreversible call or on request, because Claude fan-out spends the weekly limit (`fan-out-spends-the-weekly-limit.md`).

The verdicts are KILLED, SURVIVED SO FAR and UNTESTABLE.

## What this does not license

The attack points at claims, plans and artefacts. It never points at a person's own account of their life (`proportionate-critique.md`), and rigour about the operator's facts points outward (`good-faith-reciprocity-verification-points-outward.md`). Simple is not true, and a proposal from a child's view or a gut feel gets no discount from R0.

## Enforcement

Review and habit. No hook reads these words. V>> deleted the word counter on 2026-11-01. The counts are in `docs/measurements/2026-11-01-reasoning-lenses-retired.md`.

## Falsification

Wrong if, after 10 real uses, this rewrite has changed no decision: delete the new text. Also wrong if, over the next 10 real problems, it gives no fewer wrong causes and no less complexity than trying the simple thing first. Also wrong if planted false claims survive the ladder: fix the ladder or delete it. A ladder step that has killed nothing in 20 runs gets deleted.

Track the decisions this changed, with their evidence, and the claims each ladder step killed. Never track how often the rule is cited.

## Relationship to other rules

R0 (`r0-critical-thinking.md`) governs this rule, and its three questions are the first test of any proposal. `scientific-method.md` is the evidence side. `design-principles.md` holds KISS, YAGNI and YSH for artefacts, and Occam is their twin for explanations. `exogenous-anchor-law.md` is why the judge is never the author.

Narrative (origin, incidents, measurements, locked decisions): `docs/doctrine/reasoning-lenses.md`.
