# A Measured Claim Is Read From the Output, Never Typed From Expectation: annex

The narrative behind `rules/measured-claims-read-the-output-first.md`: its origin, incidents and measurements. The rule holds the operative text; nothing here loads at startup.

Origin: 2026-10-12, V>>: *"11 times? Learn!!"* The operator memory
`feedback_verification_claims_written_before_the_verification_ran` had reached
N=11: the eleventh instance was a one-line test fix (GRIP#8111) whose commit
message and PR body said "12 passed" while the run that was read said 9. The
memory record existed for all eleven. Per
`rules/knowledge-maturation-functor.md` a pattern at N=2 earns a mechanical
shim; at N=11 a record is demonstrably not the fix.
