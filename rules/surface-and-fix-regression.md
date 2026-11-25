# Surface and fix any regression

When you are confident that a test or check failure is a **regression** — a
behaviour that previously worked and now does not, or a genuine defect
introduced by a change — you must do two things, in this order:

1. **Surface it.** State plainly that it is a regression, what broke, and
   what changed to break it. Never bury a regression in a summary, never
   quietly admin-merge past it, never call it "pre-existing" without evidence.

2. **Fix it.** Trace the regression to its cause and fix the cause. A red
   required check is not a green — absence of a fix is not an option.

## Why

A regression is the highest-value signal the test suite can emit: it is a
defect the suite already knew how to catch, failing now. Ignoring or routing
around it discards exactly the information the suite exists to preserve.

## Scope

Applies to any test/check failure you diagnose as a regression. Does NOT apply
to a flaky or genuinely unrelated failure you have verified is not yours (in
that case, still surface it, but the fix belongs elsewhere — name whose it is).

## Falsification

This rule is working iff, when you are confident of a regression, the response
names it as a regression AND either fixes it or (for a non-owned regression)
states who owns it and the evidence — never a silent merge, never "pre-existing"
asserted without proof.
