# A Verdict Ends by Opening the Deliverable (PARAMOUNT)

Origin: 2026-08-09, V>>: *"open the deliverable — this should be a rule automatically
applied at the end of /verdict."* Rule-at-N=1 (operator-declared). Trigger instance: a
falsification report comparing a four-neurotransmitter folk model against 21 indexed
papers was delivered across four terminal responses, with the ruling, two corrections and
the citation ledger scattered between them. Every fact was present and none of it was
openable.

## The law

> **A verdict that resolves a question of substance does not end at the terminal. It ends
> by opening the deliverable — one viewable, persistent artifact the operator can open,
> keep and share, with the evidence chain intact inside it.**

## Why — this is the placement doctrine, one level up

`canonical-output-style.md` carries a HITL-locked constraint from V>> on 2026-07-31:
*"I would rather have the thinking shown fully first, and the output condensed into a
final version at the end of the response … It's awkward to go up and read important
information."* That governs **where in a response** the conclusion sits.

This governs **whether the conclusion outlives the response at all**. Scrollback fails the
same test for stronger reasons: it is scrolled past rather than scrolled back to, it
cannot be sent to anyone, it is interleaved with tool output and hook noise, and `/clear`
destroys it. A conclusion that only exists as scrollback is not a deliverable — it is a
transcript of one having been produced.

The failure is quiet, which is why it needs a rule rather than good intentions: the
terminal block *looks* complete at the moment it is written. Its insufficiency only shows
up later, when someone wants to re-read it, cite it, or forward it, and it is gone.

## What triggers it

**Substantive verdicts only.** A verdict is substantive when its reasoning and evidence
have standing value past the turn: research findings, audits, reviews, comparisons,
falsification reports, competitive analyses, anything the operator would plausibly want to
re-read or send onward.

**Exempt, and deliberately so:** a single hypothesis flipping CONFIRMED/REFUTED inside an
RSI loop, a one-line factual ruling, a pass/fail on a test. Those get the terminal card
(`lib/verdict_card.py`) and nothing more. Rendering a hosted page per loop iteration is
the ceremony this rule would otherwise become.

## What the deliverable must contain

Three parts, and the third is the one that gets dropped:

1. **The ruling** — the verdict itself, at a glance, before the argument for it.
2. **The evidence chain** — every decision-gating claim carrying its deterministic
   provenance (command + output, `file:line`, verbatim quote, DOI/PMID, URL). Evidence
   tokens render **verbatim** — never rounded, never truncated — per the same precision
   invariant the terminal card already enforces.
3. **The limits** — what could *not* be adjudicated, what rests on weaker evidence, and
   what was asserted without verification. **A verdict artifact without its limits section
   is marketing.** State the unverified as unverified inside the artifact, not only in the
   chat that produced it, because the artifact is the thing that will outlive the chat and
   be read alone.

Provenance is named per `provenance-name-the-authors.md`: the human author and the
specific assisting models, in the artifact itself.

## Safety — a rendered artifact is a leak surface

Before publishing, sweep the content against `visual-artifact-leak-prevention.md` and
`no-sensitive-info-in-grip.md`. A verdict on internal work plausibly carries client names,
internal hostnames, repo paths, personal identifiers, or third-party names quoted from a
message. Artifacts start private, but private is a default, not a guarantee — the operator
may share it in one click, and that click is the whole point of the rule.

Strip third-party names that add nothing (an addressee's first name in a quoted message
earns no place in a shareable page). Where client content is genuinely the subject, the
artifact is client-scoped and never published on a GRIP-public surface.

## Mechanism

The Artifact surface is the natural fit — self-contained, private by default, openable in
a browser, shareable when the operator chooses, and redeployable to the same URL when the
verdict is revised. Where that surface is unavailable, a rendered PDF
(`professional-pdf`) or a written file under a durable path is the fallback, and anything
that must survive a repo operation is additionally copied out via `bin/grip-backup`
(`paramount-backups.md`).

The invariant is **openable and persistent**, not *hosted*. Return the link or path in the
closing block so the operator does not have to go looking for the thing that was just made
for them.

## Falsification

Wrong if, over the next ~10 substantive verdicts: the artifacts are never re-opened,
cited, or forwarded (write-only ceremony — measure opens and references, never publish
count, or this rule Goodharts into artifact spam); OR the render cost exceeds the value of
the persistence for verdicts below some size, in which case raise the substantive
threshold rather than dropping the rule; OR the operator consistently copies the terminal
block out by hand anyway, which would mean the terminal block was already sufficient and
this is solving a problem that does not exist. Track: substantive verdicts shipped without
an openable deliverable (target 0), and re-opens per published verdict artifact.

## Enforcement status (verify-canonical — do not overclaim)

Wired **now**: `skills/verdict/SKILL.md` carries this as an automatic closing step, in its
frontmatter description and a dedicated section. That is the operator's stated ask and the
whole of what is mechanically in place.

**Not yet wired**, though named in scope below: `/falsify-self`, `/ultrajudge`,
`/reviewing-contributions`, `/research-council`, `/recap`. Those are review-and-habit until
a skill edit lands, and this rule does not claim otherwise. No mechanical gate exists for
any of them — a hook checking whether a substantive verdict produced an artifact would need
a reliable substantive/trivial discriminator, which is a judgement call, and a gate that
guesses wrong either spams artifacts or blocks a legitimate one-line ruling. Held at review
tier deliberately per `knowledge-maturation-functor.md`; revisit if verdicts keep shipping
unopened.

## Scope

Every `/verdict` on a substantive question, and by extension the closing step of any
skill that produces a ruling with standing evidence — `/falsify-self`, `/ultrajudge`,
`/reviewing-contributions`, `/research-council`, `/recap`. Sits beside
`canonical-output-style.md` (the placement doctrine this extends),
`prove-it-claims-canonical.md` (the evidence chain the artifact must carry),
`visual-artifact-leak-prevention.md` (the sweep before publishing),
`provenance-name-the-authors.md` (who and what produced it), and
`paramount-backups.md` (anything whose loss would hurt). Mirror into `~/.hal/rules/` if
HAL grows a verdict surface.
