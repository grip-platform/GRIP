# Deferred Follow-ups: Complete or Surface — Never Silently Drop (PARAMOUNT)

Origin: 2026-07-15, V>>: *"not going ahead with deferred followups (AND NOT
REMINDING ME) is a violation of the 'Completeness' doctrine — no stubs, no
theatre, this is not YAGNI, it is a violation of YSH."* Rule-at-N=1 (operator-
declared PARAMOUNT; ties directly to the Completeness principle which is itself
PARAMOUNT, `no-stubs.md`).

## The Law

> A deferred follow-up — an AGORA `DEFER_CHANGES`, a "tracked follow-up", a "next
> wave", a `TODO`, an "honestly flagged for the review pass", a parked task — is a
> **DEBT with a PROVEN need**, not a maybe. It has exactly **three honest exits**:
> **(a) COMPLETE it**; **(b) actively SURFACE it** (carry it in the anti-drift
> Remaining / Open / Needs-you block of EVERY relevant report) until it is
> completed or closed; or **(c) STALE it — a RECORDED won't-do**: an explicit,
> dated, reasoned closure on the record (a task set to `deleted` / won't-do with a
> reason, or a struck line in the tracking artefact). **Silently deferring it and
> never returning — AND not reminding the operator — is forbidden.** It is a
> Completeness violation, theatre, and a YSH violation.
>
> The STALE exit is the ONLY way to stop surfacing a DEFER short of completing it,
> and it is a WRITE, never a silence: "don't remind me of X again" closes X as a
> recorded won't-do (reason + date), so a future reader can tell *explicitly-closed*
> from *silently-dropped*. An un-recorded stop is the forbidden silent-drop wearing
> a closure's clothes.

## A discovery is not a follow-up until it is RECORDED (2026-08-25)

Origin: V>>, on being handed a defect in prose and nothing else — *"if you realise
there are mistakes you should not mention them and take no action. that doesn't make
sense."*

The three exits above assume the item is already IN a tracking system. The failure
this closes happens one step earlier, at the moment of NOTICING, and it wears the
costume of diligence:

> **Naming a defect in a chat message is not exit (b).** Exit (b) is carrying it in
> the Remaining block of EVERY relevant report until it closes — a one-off paragraph
> at the end of one message satisfies none of that, and it evaporates at session end.
> A discovery that is mentioned and not recorded has taken NO exit. It is the silent
> drop, delivered in a tone of candour.

So a defect you find mid-work has exactly two honest terminal states: **FIX it**, or
**RECORD it durably** (an issue, a task — something with a URL that outlives the
window). Only after one of those does mentioning it in the report mean anything;
before that, the mention IS the drop.

**The instance.** Investigating a `/save` timeout on 2026-08-25 I measured the
instance serializer at ~40s and established it scales with session length — so the
budget raise I had just shipped was a holding action that would fail again. I wrote
exactly that in the closing report, filed nothing, and moved on. Everything in the
sentence was true, which is what made it worse: an accurate description of a known
defect, addressed to someone who then has to remember it for me. It became GRIP #5069
only because V>> asked.

**Why the honest-sounding phrasing is the tell.** "Worth flagging separately", "I
haven't touched that", "out of scope for tonight", "a bigger change than this" — each
reads as scope discipline and each, unaccompanied by a record, is the drop. Scope
discipline is a reason not to FIX something now. It has never been a reason not to
RECORD it, because recording costs one tool call and is the entire difference between
a defect the project knows about and one only a transcript remembers.

**Falsification.** Wrong if, over the next ~20 mid-work discoveries: filing them
produces a tracker no one reads, so the records are pure ceremony (then keep the fix
half and drop the record half — but measure reads, not files); OR the discipline
converts into issue-spam for trivia a reader would never act on (then raise the bar
to defects that would change someone's decision, never back to prose-only). Track:
defects named in a report with no durable record (target 0; was 1 on 2026-08-25).

## Why — YSH, not YAGNI (the load-bearing distinction)

A DEFER is, *by construction*, a **proven** need — it was identified as necessary
and then postponed for sequencing (blocked, needs a separate PR, outside the
current atomic scope), NOT judged unnecessary. That is exactly **YSH**'s domain
("You Should Have — when the need is proven, do it now"), the dialectical INVERSE
of **YAGNI** ("don't build speculative / unproven things"). Invoking
"deferred ⇒ YAGNI, so I need not do it" is a category error: YAGNI skips
**unproven** needs; a DEFER is a **proven** one.

So a silently-dropped DEFER is **`no-stubs` at task granularity**: the record
(a task, a PR follow-up note, a "tracked for later") implies the work is coming,
a future reader / the operator believes it is coming, and then it never does — the
exact false-completeness signal that makes a future session confabulate "that's
handled." A hollow promise of future work is a hollow symbol.

## Mandate

- **DEFAULT IS DO.** If a deferred item can be done now (or in the next actionable
  window), do it now — deferral is for genuine sequencing, never for making work
  disappear (YSH).
- **If you must defer, you incur a SURFACING debt.** The item appears in the
  anti-drift Open / Remaining / Needs-you block of every report from now until it
  is completed or STALE-closed (`session-protocol.md` Rule 11, which exists and
  carries DEFERs). A report that omits a live DEFER is the violation — **except**
  when the DEFER was lost to an unscheduled context reset (a crash / compaction the
  agent cannot recover from): then the agent ACKNOWLEDGES the gap on `/back` (no
  violation) and, on recovering the item, routes it to one of the three exits.
- **NEVER a silent drop.** A DEFER with no owner, no next-actionable window, and no
  surfacing is a stub — COMPLETE it, SURFACE it, or STALE it (the recorded won't-do,
  reason + date). Dangling is forbidden; so is an *unrecorded* "stop reminding me".
- **On `/back`, `/eod`, closeout, and every substantive report**, enumerate the
  open DEFERs. "I deferred X" without "…and here it is again / here it is done /
  here it is closed-as-won't-do" is the failure this rule exists to kill.

## Relationship

Completes `no-stubs.md` (Completeness) at task/follow-up granularity; enforces
**YSH** (`efficiency-rules.md` principles + `CLAUDE.md`); bounded by
`loop-priority-external-over-theatre.md` (a DEFER that never ships is internal
theatre); operationalised by anti-drift Rule 11 (`session-protocol.md` /
`efficiency-rules.md` — Open/Remaining carries DEFERs); a DEFER is an open→closed
lifecycle record so `lifecycle-closed-or-expired.md` applies (needs a closer or a
TTL-surfacing). AGORA `DEFER_CHANGES` (`ci-review-driven-convergence.md`) FILES
the follow-up; THIS rule binds that filed follow-up to completion-or-surfacing.

## Falsification

Wrong if, over ~20 DEFERs it governs: forcing completion-or-surfacing produces
more churn/noise than the silent-drops it prevents (→ relax the surfacing cadence,
keep the do-default); OR every deferred item would have been completed anyway
without the rule (inert → drop); OR the operator asks to STOP being reminded of a
specific DEFER AND that stop is RECORDED as a won't-do (reason + date) — that is
the STALE exit working, not the rule failing (an *unrecorded* stop is instead the
silent-drop the rule forbids, so it does NOT falsify the rule).

**Decay-metric falsifier (the anti-accretion guard).** The SURFACE exit must not
become a silent-accretion backdoor (the `lifecycle-closed-or-expired.md` failure at
task granularity): a DEFER perpetually re-surfaced but never COMPLETED or
STALE-closed is decaying, not being honoured. Track DEFER **age** and
**surface-count**: a DEFER that survives **> N surfacing cycles OR > T days**
(default N=10 reports / T=30 days) without moving to COMPLETE or STALE is a **decay
violation** — the rule (or the operator's prioritisation) is failing, and the item
is force-triaged (do it, or STALE it with a reason). Conversely, if over a quarter
the STALE bucket swells while COMPLETE stays flat (operators STALE-ing to silence
rather than to decide), the STALE exit is being abused → tighten what counts as a
valid won't-do reason, never re-open the silent-drop path.

Track: DEFERs silently dropped (neither done, surfaced, nor STALE-recorded) per
month — target 0; DEFER decay rate (open DEFERs older than T with no exit) —
target trending to 0. Motivating failure 2026-07-15 (the onboarding-interview
review DEFERs were deferred without a commitment to do them AND without reminding
the operator).

## Scope

Every DEFER / tracked follow-up / TODO / "next wave" / "flagged for later" on
every surface in GRIP and HAL: AGORA `DEFER_CHANGES`, PR follow-up notes, the
harness task list, in-report deferrals, "honestly deferred" PR sections, and the
equivalent surfaces in HAL (this rule is mirrored into `~/.hal/rules/`).
