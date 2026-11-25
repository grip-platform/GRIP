---
name: productise
description: "Shapes something GRIP built into a candidate product that a stranger could pick up, use and pay for, the KISS way. It names the buyer and who already ships the same thing, cuts the thing down to its first minute of use, puts every screen in plain words, packages one artefact, and registers a stranger test with a deadline. Only a passed test, with strangers paying, makes it a product. Use when the operator says /productise, productise this, make this sellable, turn this into a product, or package this for customers, and whenever a tool, game, page or service is about to reach people outside the team, even if nobody says the word product. Applies rules/productise-the-proof.md."
status: Active
version: 1.0.0
triggers:
  - /productise
  - productise this
  - make this sellable
  - turn this into a product
  - package this for customers
integrates:
  - why-not-just
  - ultraideate
  - plain-language
  - authentic-writing
  - redact
---

# /productise

Everything GRIP builds can become a product, but almost nothing should be sold as it stands.
A product needs a buyer, a smaller shape, plain words and a test with strangers.
This skill does that work in seven steps and writes one short page, `PRODUCT.md`, in the product's own repository.

The doctrine is `rules/productise-the-proof.md`: build every capability so that a stranger could use it.
Call it a product only when a buyer and a replayable proof back the claim.

## The seven steps

Work through this checklist from the top.

```text
- [ ] 1 Buyer          who pays, for which job, and who already ships it
- [ ] 2 Proof          one claim a stranger can check, and the check
- [ ] 3 Cut            keep the first minute, cut or hide the rest
- [ ] 4 Words          plain, short, no story
- [ ] 5 Package        one artefact, a name, a pitch, a price hypothesis
- [ ] 6 Privacy        fakes in, real data out
- [ ] 7 Stranger test  registered, with a deadline and a kill condition
```

### 1. Buyer

Write one sentence: who pays, and for which job.
"Teams whose stand-ups run over" is a buyer. "Everyone" is not.
Name who already ships the same part, and say where you searched, because a part that others ship is no moat.
If no buyer comes to mind, stop, because the thing is infrastructure.
Record that in `PRODUCT.md`, and keep the thing useful rather than sold.

### 2. Proof

Name the one claim that a stranger can check without trusting you.
Ship the check with the product.
A stranger checks "works offline" with every connection off, Wi-Fi and Bluetooth included, from start to end of use.
A stranger checks "sends nothing" in the network panel, which must show no request in one full session.
Each check covers only what it watched, so the page says that too.
A seal proves integrity, never correctness, so never sell more than the check shows.

### 3. Cut

The product is what the buyer does in the first minute.
Keep that, and cut or hide everything else.
Ask of each screen, step and setting: why do we need this, and what can we throw away (`/why-not-just`)?
A safety rule stays, such as consent, privacy or a free Pass, but it gets shorter, not longer.
When the right cut is unclear, run `/ultraideate` and let the operator pick.

### 4. Words

Buyers do not read.
Every surface they see uses plain words, and as few as possible (`/plain-language`, `/authentic-writing`).
A rule fits on one line of about eight words, and a button says what happens.
Write no story, no lore and no paragraph that explains a feature.
If a feature needs a paragraph, make the feature obvious or cut it.

### 5. Package

Produce one artefact that a stranger can get and run with no help: a file, one install command, or a URL.
Give it a name, a one-line pitch and a store text of at most 50 words.
Write a price hypothesis and its basis, which the operator turns into a price, because pricing is commercial.

### 6. Privacy

Real names, client data and personal matters never ship.
Use obvious fakes in anything shared, and keep real names in a local file that git ignores.
Client data and other people's personal data go in a dedicated private repository, never in a product or GRIP repository (`rules/no-sensitive-info-in-grip.md`).
Run `/redact` on text that leaves the building (`rules/personal-matters-stay-private.md`).

### 7. Stranger test

Register the test with `lib.hypothesis_engine.register`.
Name who tries it, what counts as a pass, the deadline, and what kills it.
Give the engine a `metric`, a numeric `prediction`, an ISO `deadline` and a `measure_plan`.
The plan says how you count the number, and the engine refuses a new metric without one.

A pass needs strangers who pay, or who pre-order at a real price, and for whom the check from step 2 holds.
Use alone keeps the thing a candidate.
Never call it a product before a pass.

## The page: PRODUCT.md

Keep the page under 40 lines, and delete any line you cannot fill.

```markdown
# <Name>

Status: candidate | product | infrastructure, since <date>
Buyer: <who pays, for which job>
Pitch: <one line>
Already ships: <who ships the same part, and where you searched>
Proof: <the claim> -- check: <how a stranger checks it, and what it covers>
First minute: <what the buyer does>
Cut: <what went, or went behind a menu>
Package: <file, install command or URL>
Store text: <50 words at most>
Price: <hypothesis and its basis> -- the operator decides
Stranger test: <who, pass with payment, deadline, kill> (<hypothesis id>)
Not claimed: <what this does not promise>
```

## Example (invented)

A one-file stand-up timer, after a run of this skill:

```markdown
# Ten Minutes

Status: candidate, since 2026-11-22
Buyer: team leads whose daily stand-up runs past ten minutes
Pitch: A stand-up timer that ends the meeting at ten minutes.
Already ships: Tock and Turnly, two invented rivals that time stand-ups too; this one only starts faster (searched the Apple App Store and Google Play)
Proof: works offline -- check: every connection off, Wi-Fi and Bluetooth too, then run one stand-up from Start to the ten-minute stop; this covers that workflow only
First minute: tap Start, then Next for each speaker
Cut: themes, sounds and statistics went behind the menu
Package: one HTML file
Store text: Tap Start. Each speaker gets a turn and a bar that fills. At ten minutes the meeting ends.
Price: 30 USD a team per year, under the timers already sold -- the operator decides
Stranger test: five teams outside the company for two weeks; pass = three teams buy a year and the offline check holds for each; kill = none buys (hypothesis id from lib.hypothesis_engine)
Not claimed: shorter meetings in general
```

## What this skill does not do

- It does not publish, post or sell, because those steps are the operator's and cannot be undone.
- It does not set a price or terms, because those are commercial decisions.
- It does not claim a moat. It names a competitor that ships the same part, and never hides one.
- It does not add features to make a thing look like a product. Cutting is the usual win.

## Falsification

This skill is wrong if, over its next five runs, a thing that it marks as a candidate reaches no stranger test within 30 days.
It is also wrong if its `PRODUCT.md` pages grow past 40 lines, or if operators productise by hand and get better results.
Track three numbers: runs, stranger tests registered, and stranger tests passed.
Success is a passed stranger test, never a page written.

## Related

- `rules/productise-the-proof.md` -- the doctrine that this skill runs.
- `/why-not-just` and `/ultraideate` -- the cut and the redesign.
- `/plain-language` and `/authentic-writing` -- the words.
- `/redact` -- the privacy step.
- `rules/knowledge-maturation-functor.md` -- GRIP applied the doctrine by hand at least twice before this skill existed, which is the N=2 that earns a skill.

## Changelog

| Version | Date | Change |
|---|---|---|
| 1.0.0 | 2026-11-22 | First version, written on the operator's request. |
