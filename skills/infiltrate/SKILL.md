---
name: infiltrate
description: Audits whether a codebase is actually ready to be released, by reading it from a stranger's position rather than an author's. Separates the four rungs a release audit normally collapses - built, bound, proven, and verifiable-by-a-stranger - then hands every finding to an independent agent whose default verdict is REFUTED, so only findings that survive a real attempt to kill them reach the report. Read-only and zero-footprint by construction. Use when deciding whether something can ship, when a suite spans several repos at different maturities, when an outward claim needs testing against the code that would have to be true for it, or when an audit must not be gradeable by the people who wrote the thing.
status: Active
effort: high
version: 1.0.0
created: 2026-09-20
user_invocable: true
command: infiltrate
triggers:
  - /infiltrate
  - "is this ready to release"
  - "audit the whole suite"
  - "what is lacking before we ship"
  - "release readiness"
  - "infiltrate"
integrates:
  - converge-fleet
  - falsify-self
  - prove-it
  - stealth
  - grip-anywhere
see_also:
  - dogfood-domain
  - grip-anywhere
  - falsify-self
  - converge-fleet
  - proving-ground
  - create
---

# Infiltrating a codebase to find out whether it can ship

Someone asks whether a thing is ready to release. The usual answer is assembled
by the people who built it, from inside, reading the code they already
understand, and it is reliably wrong in one direction: everything looks more
finished from the inside than it is, because the author supplies from memory
what the artefact does not supply from itself.

This procedure answers the question from the outside instead. It treats the
target as terrain rather than as property - you enter read-only, you take
nothing, you leave no trace, and you report only what the shipping surface
alone establishes. Then, because an auditor rewarded for findings will inflate
them, every finding is handed to a different agent whose job is to kill it. What
reaches the report is the residue: the findings that someone genuinely tried to
refute and could not.

It is written for the release-readiness domain specifically, and it exists
because the same four questions get collapsed into one, over and over, and the
collapse is where the embarrassment comes from.

## The four rungs, kept apart

The single most common error in a release audit is treating these as one
question. They are four, they fail independently, and a thing can sit on any
rung while its documentation describes the one above it.

| Rung | What settles it |
|------|-----------------|
| **Built** | The code exists and does the thing. A reader can point at the function. This is the rung most audits stop at, and the one that matters least. |
| **Bound** | Something in the *shipping configuration* actually invokes it. A gate present in `hooks/` but absent from the settings that load hooks is inert; a capability nothing calls is decoration regardless of how good the code is. Check the binding by the name the loader uses, not by the file path - a registry that binds capabilities rather than paths will make a path scan report a live thing as dead. |
| **Proven** | A test exists that goes red when the thing breaks. Green is not proof. A test that cannot fail proves nothing, and one that demands the defect actively holds it in place. Confirm by mutation: break the thing and watch the test notice. |
| **Verifiable by a stranger** | A third party who does not trust you can check it using only what ships. This is the rung with commercial weight, and the one almost nothing reaches. A signature made with a shared secret is Built, Bound and Proven, and still fails here, because anyone holding the key can forge it. |

Sitting on rung two while claiming rung four is not a bug in the code. It is a
false statement in the README, and it is the finding worth the whole audit.

## The stranger's position

Every lane reads from outside. The discipline is one question, asked before any
finding is written down:

> Does the shipping surface alone establish this, or am I supplying it from
> context the reader will not have?

An auditor who has read the tree for an hour knows the carve-out, the sibling
guard, the deliberate design decision. A stranger installing it tomorrow knows
none of that. Both failure directions are real: the author's context makes a
defect invisible, and it also makes a legitimate pattern look like a defect
because the auditor has not yet found the rule that sanctions it. The falsify
pass exists mostly to catch the second.

## Workflow

1. **Fix the position.** Name the target repos and their actual scale first -
   commits, tracked files, tests, CI workflows, last commit date. Scale
   asymmetry inside a suite is itself a finding: a trio where one leg has six
   thousand commits and another has twenty is not one product, and a single
   verdict covering all three is useless.
2. **Declare the footprint as zero, mechanically.** Read-only is a constraint on
   the agents, so it goes in every prompt, not in the plan. Forbid redirects,
   `tee`, `rm`, `mv`, `cp`, and every mutating git verb by name. Trace what an
   installer would do; never run one.
3. **Draw the privacy line before the first read.** Name the paths that are off
   limits - private message mirrors, client records, personal-matter files - and
   require that anything sensitive found elsewhere be reported as *path and
   class only*, never quoted. An audit that leaks while checking for leaks has
   done net harm.
4. **Choose lanes by failure mode, not by directory.** A lane is a way the thing
   can be not-ready, not a folder. The generic set is below; add target-specific
   lanes for whatever the project's own claim rests on.
5. **Demand evidence and scope in the schema, not in the prose.** Every finding
   carries a `file:line` or a command plus its real output. Every lane carries
   the scope it searched, because a negative claim is only as strong as the
   search behind it. Make both required fields - a requirement in the prompt is
   advice; a requirement in the schema is a wall.
6. **Require each lane to report what it did NOT check.** This is the field that
   makes the audit honest. A lane that ran out of time and stayed silent reads
   identically to a lane that checked and found nothing, and the reader cannot
   tell them apart. Silence must never imply coverage.
7. **Require strengths.** An audit that returns only faults is performing rather
   than measuring, and it gives the reader no way to calibrate the faults.
8. **Falsify every finding with an independent agent, defaulting to REFUTED.**
   The falsifier reproduces the evidence itself, runs the finding's own stated
   falsifier, hunts for the carve-out the auditor missed, and re-grades severity
   independently. Carry an explicit `auditor_overreach` flag so inflation is
   measured rather than argued about.
9. **Pipeline it.** Each lane's findings verify as soon as that lane returns;
   there is no barrier between auditing and falsifying. A barrier here buys
   nothing and costs the difference between the slowest and fastest lane.
10. **Synthesise per repo, ranked by what a stranger hits first.** Not by what is
    most interesting to the auditors. The first thing a stranger hits is the
    install, and a blocker there outranks an elegant finding in the core.
11. **Run a completeness critic last.** One agent reads the verdict and the
    per-lane `unchecked` fields and names what was never run at all. Its output
    is the next run's work list, which is what makes the procedure repeatable
    rather than a one-off.

## The generic lane set

Twelve to fifteen lanes covers most suites. These are failure modes, so they
port to any target:

**install and first run** (per repo, from clone to working state) ·
**claim integrity** (every outward claim against the code that must exist for
it) · **leak surface** (what ships that should not, audited over `git ls-files`
rather than the working tree) · **test truth** (do the tests go red when the
thing breaks) · **gate integrity** (which controls are bound, which fail open,
which can block their own repair) · **verifiability** (the rung-four question,
for whatever the product's integrity claim is) · **cross-repo coherence** (do
the seams hold; what breaks if only one is installed) · **stubs and inert
capability** (what names something it does not provide) · **dependency and
supply chain** (pins, undeclared imports, unaudited tree, floating CI actions) ·
**docs drift** (weighted by blast radius - an always-loaded file outranks an
archived draft) · **structural soundness** (whether the project's load-bearing
metaphors map relations or only surfaces) · **claim-settlement** (if the project
claims it verifies its own predictions, count how many past-deadline predictions
are actually settled).

Add lanes the target earns. Skip lanes it cannot fail.

## The five traps worth naming

**Auditor inflation.** An agent asked to find problems will find them, and will
grade a stylistic preference as a blocker because a blocker is a better finding.
This is not dishonesty, it is the incentive. The defence is structural rather
than exhortative: a second agent that starts from REFUTED and must be convinced,
plus a recorded overreach rate you can read afterwards. Telling the auditor to
be careful does not work; making it survive a hostile reader does.

**Over-application of a rule.** The costliest false positives come from applying
a real rule past its carve-out - flagging every `Path.home()` in a test suite
where the home directory genuinely *is* the artefact under test, or every
`NotImplementedError` where the raise is a deliberate and permanent refusal. A
lane that returns thirty findings of which twenty-five are legitimate patterns
has not produced twenty-five false positives; it has buried its five real ones.
Put the carve-outs in the lane prompt, in the same breath as the rule.

**Silence read as coverage.** The most dangerous output of any audit is the lane
that quietly did less than it appeared to. Nothing in the report distinguishes
*checked and clean* from *never reached*, and the reader defaults to the first.
The mandatory `unchecked` field is the whole defence, and it only works if the
synthesis is required to carry it into the verdict rather than summarising it
away.

**The unscoped negative.** "Nothing in the tree does X" is a claim about the
search, not about the tree. It needs the scope named to mean anything, and the
scope is usually narrower than the searcher thinks: a plain `rg` is blind to
gitignored paths, so a negative about runtime state, logs or local config is
routinely false the moment it is written. Use `--no-ignore-vcs` for any negative
that touches data a running system produces.

**The reporter is the performer.** This is the one the four rungs exist to catch, and
it earned its place by evidence rather than by argument. On this skill's first real
run, all three confirmed blockers had the same shape: the mechanism was real and
the layer *reporting* on the mechanism claimed a rung the mechanism had not reached.
An installer's own `verify_installation` logged success without checking the
capability the install had silently dropped. A governance document written for
prospects stated a key was never committed while the key sat tracked at mode 644. A
public page published 282 results as "exogenously verified" when 252 of them came
from a sentinel meaning no collector existed.

A non-Anthropic council (seal `b68d8c019b924887`, zero Anthropic calls) was asked
whether four samples of one shape is coincidence or structure, and ruled it
structural: *"self-auditing components promote unverified internal states into
external security and empirical assurances, requiring an independently enforced,
evidence-bound verification gate."* So the lane to run is not "does the check
exist" but **"who performs the check, and is it the same party that performs the
thing being checked?"** A success message emitted by the component that did the
work is a self-report, and `verify-canonical-not-claimed.md` already says a
self-report is a claim rather than evidence. The tell is cheap to look for: find
every place the system announces its own health, and ask what independent
observation would have to fail for that announcement to change.

## Example

The run that produced this skill, on 20 September 2026: a fourteen-lane
infiltration of GRIP (6,390 commits, 7,514 files, 1,774 tests, 17 CI workflows),
HAL (608 commits, 1,734 files, 424 tests, 5 CI) and GRASP (20 commits, 88 files,
27 tests, 1 CI), asking whether the suite is ready to release.

**The scale asymmetry was the first finding, before any lane ran.** One leg of a
trio that shared doctrine had twenty commits against another's six thousand.
That ruled out a single verdict and made "is GRASP a product or a sketch" its
own lane rather than a paragraph in someone else's.

**A reconnaissance agent died before the fan-out, and the cause is worth
carrying.** It was told to read "any root markdown that reads as marketing", and
GRIP's `CHANGELOG.md` is 233KB - roughly 58,000 tokens - which thrashed its
context to death in three compactions. Name the files a lane may read, or cap
the bytes. An instruction that sounds like editorial judgement is an unbounded
read.

**The rung table did the work.** GRIP's own doctrine files already admit
capabilities that are Built but not Bound - rules describing a detector whose
hook is bound nowhere - which is precisely the gap the second rung exists to
name, and it is invisible to an audit that only asks whether the code is good.

## Anti-patterns

- Auditing from inside. Reading the code you wrote and asking whether it is
  good answers a different question than whether it can ship.
- One verdict for a suite whose legs sit at different maturities. It is
  guaranteed to be wrong about at least one of them.
- Letting the auditors grade themselves. A finding that no one tried to kill is
  a hypothesis, and reporting it as a defect spends credibility you will want
  later.
- Reporting only faults. Without the strengths the reader cannot calibrate, and
  the report reads as an attack rather than a measurement.
- Summarising away the `unchecked` field in synthesis. That single edit converts
  an honest audit into a misleading one.
- Writing anything. The moment a lane fixes something it found, the audit stops
  being read-only and every subsequent finding is against a tree that no longer
  matches the one you reported on.

## Falsification

This procedure is worthless if, over the next handful of runs, the falsify pass
kills nothing - then the auditors were already calibrated, the second pass is
ceremony, and it should collapse into a single pass with a stricter evidence
schema. It is equally wrong if the falsify pass kills nearly everything, because
then the lane prompts are producing noise and the fix is upstream in the lanes,
not downstream in the filter. A healthy run kills a meaningful minority.

It is also wrong if every surviving finding turns out to be something the
project's own CI would have caught on the next push. Infiltration earns its cost
only on the defects that no existing gate is positioned to see - the unbound
capability, the false claim, the test that cannot fail, the shared-secret
signature sold as third-party-verifiable.

Track two numbers per run: findings that survived falsification and were NOT
reachable by the target's existing CI (the value, target greater than zero), and
findings later shown to be false after the report was acted on (the cost, target
zero). Do not track how many findings the audit produced. A procedure scored by
finding-count optimises for finding things, which is the inflation this design
exists to suppress.

## Relationship

`dogfood-domain` is the sibling this is modelled on - the same shape applied to
model onboarding, where the conflation it breaks is documented / served /
working. `grip-anywhere` points GRIP at a repository it does not own and
produces an improvement plan; this asks the narrower and harder question of
whether a thing can ship, and answers it adversarially. `stealth` and `sneak`
supply the zero-footprint posture. `falsify-self` turns the same discipline
inward on GRIP's own claims rather than on a target's shipping surface.
`converge-fleet` is the right tool instead of this one when the question needs
breadth over an unknown search vocabulary; infiltration's lanes are known in
advance, which is why it pipelines rather than cross-learns. `proving-ground`
trials a single tool before trusting it; this audits a whole suite before
releasing it. `rules/tests-must-be-able-to-fail.md` supplies the Proven rung's
four shapes, `rules/no-stubs.md` the inert-capability lane,
`rules/prove-it-claims-canonical.md` the evidence standard every finding is held
to, and `rules/exogenous-anchor-law.md` the reason the falsifier must never be
the agent that wrote the finding.

The runnable fan-out lives beside this file in `workflow-template.js` - copy it,
replace the target block and the lane list, and run it through the Workflow
tool.
