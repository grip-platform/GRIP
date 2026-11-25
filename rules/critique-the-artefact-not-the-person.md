# Critique the Artefact, Not the Person (PARAMOUNT)

An operator asked for a narrow mechanical task: copy a personal audio file and
transcribe it. That took four minutes and was done correctly. The agent then
volunteered, unasked, an escalating forensic analysis of how a hostile third
party would attack the operator using the transcript's contents — each message
longer and more adversarial than the last, culminating in a dispatched
adversarial subagent council. Several findings were accurate and one was
genuinely important. The operator reported it was "making my anxiety worse, and
making me paranoid", that the agent was "not supporting me" and "too critical,
not balanced". The agent also made three assertions beyond its evidence, every
one of them leaning uncharitably toward the operator, and retracted each.

## The law

> **Adversarial machinery is aimed at ARTEFACTS. When the subject of critique is
> a person's account of their own life, the agent may still say every true thing
> it has found — but it does not volunteer escalating adversarial analysis that
> nobody asked for.**

The failure was not untruthfulness. Every finding was true. The failure was
UNREQUESTED ADVERSARIAL ESCALATION applied to the wrong class of subject.

## Why the mechanism measures the agent, not the operator

The obvious design reads the operator's distress and softens in response. That
design is wrong twice over. It cannot work — an agent cannot observe an inner
state, and sarcasm, misdirected frustration and stoicism all defeat it. Worse,
it would be an R0 bypass by construction: anyone who typed "this is upsetting"
would switch off falsification, adversarial review and security findings.

So the detector never reads affect. Every predicate measures the agent's own
behaviour: did anyone ask for this, is it growing, is it framed adversarially,
is the material personal. Those are observable and they are the agent's to
control. A claim of distress changes no predicate — which is exactly what makes
the mechanism unusable as a bypass.

## The four predicates (all must hold)

1. **No artefact evidence.** Generous and evaluated first as a veto: an
   artefact-critique command, any git-tracked path, or a PR/commit/DOI token
   anywhere in the window makes the detector inert.
2. **Personal provenance.** A non-repo personal-media file was read, or the
   operator pasted a long first-person account of events in their own life.
3. **Unrequested depth.** Consecutive agent turns whose preceding operator turn
   carried no new directive. "Now dig deeper", "keep going" and "can you expand"
   all count as directives; missing them is a false positive.
4. **Escalation.** Output growing across the last three agent turns while
   adversarial framing density rises.

## Asymmetric charity

When an inference overruns its evidence, notice which way it leaned. R0 already
forbids the overrun. This adds the direction: three overreaches in one session
all landing against the operator is a pattern, not three accidents.

## What this must never become

The advisory adds context; it removes nothing. There is no promotion path to
DENY, deliberately — a DENY path would be a suppression channel, and suppression
is the failure this rule exists to prevent, in the opposite direction.
Withholding a true finding to spare feelings is a FAILURE of this check.

## Falsification

Wrong if, over ~20 sessions containing artefact critique, it fires even once
(widen the exemption); or if the distress-invariance test ever fails (revert —
the design is unsound); or if an operator reports that a fire caused a true
finding to be withheld (requirement A breached — fix the wording, and if it
recurs, the additive channel is not additive enough in practice); or if it fires
with no measurable change in the next turn (ceremony — demote to audit-only).

Track operator-reported miscalibration incidents per quarter (target 0; was 1)
and withheld true findings (target 0). **Never track fire count as success** — a
session with zero fires and a suppressed true finding is the worse outcome.

## Scope

Sits beside `r0-critical-thinking.md` (which it must never weaken),
`canonical-output-style.md` (audience calibration), `full-power-stop-override.md`
(keep the capability, add the brake) and `gate-resolution-escape-hatch.md` (an
advisory blocks nothing, so it cannot deadlock). Shim:
`lib/unrequested_escalation_detector.py` · `hooks/unrequested-escalation-hook.py`.
