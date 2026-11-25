# Anchor on the Artefact, Not the Session

## The rule

> **Rank interpretation hypotheses by what the ARTEFACT affords, never by what the
> SESSION has been about. Recent context is availability bias, not evidence.**

Corollary, and the part that is mechanically checkable:

> **Run the cheapest family-discriminating measurement BEFORE committing to an
> interpretation.** For an unknown symbol stream that means entropy, the run-length
> distribution, and longest-exact-repeat against a random control — seconds of work
> that eliminate whole families of hypothesis at once.

## Why this failure is invisible from the inside

Anchoring does not feel like a guess. The wrong hypothesis arrives feeling
*contextually appropriate*, which is exactly what suppresses the check. So the tell
has to be procedural rather than introspective: **notice when you have chosen a
parameter before you have taken a measurement.** I picked a sample rate before I
computed entropy. The sample rate is a commitment to a hypothesis; the entropy is
the test of it. Doing them in that order is the bug, and it is visible without any
self-knowledge at all.

## Scope

Every unknown artefact handed over mid-session: byte streams, encodings, log
formats, file fragments, unfamiliar data, an error with no obvious source. It binds
hardest when the session has a strong recent theme, because that is precisely when
the contaminated prior feels most natural.

This does NOT mean discard context. Session context is a legitimate *tiebreaker*
between hypotheses the artefact already supports. It is not a substitute for the
artefact's own affordances, and it never outranks a cheap measurement.

## Falsification

Wrong if, over the next ~10 unknown-artefact tasks, the session-context prior
proves right more often than the artefact-intrinsic prior — then anchoring carries
real information, and this should narrow to "name which prior you are using and
why" rather than "prefer the artefact". Also wrong if measure-first costs more than
the misdirection it prevents on artefacts that are trivially identifiable (a file
with an obvious magic number needs no entropy calculation).

Track: turns spent on a hypothesis later abandoned wholesale (target: the
measurement precedes the commitment, so this trends to zero).

## Siblings

`r0-critical-thinking.md` (falsify before believing — this extends it from claims
to hypothesis *ranking*) · `prove-it-claims-canonical.md` (a negative claim must
name its search scope) · `verify-canonical-not-claimed.md` (measure the thing, do
not trust the plausible story about it).

Narrative (origin, incidents, measurements): `docs/doctrine/anchor-on-the-artefact-not-the-session.md`.
