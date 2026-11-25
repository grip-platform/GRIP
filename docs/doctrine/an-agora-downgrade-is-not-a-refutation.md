# An AGORA Downgrade Is Not a Refutation: annex

The narrative behind `rules/an-agora-downgrade-is-not-a-refutation.md`: its origin, incidents and measurements. The rule holds the operative text; nothing here loads at startup.

Origin: 2026-09-14, at N=3. Promoted from
`memory/feedback_agora_downgrade_is_form_not_substance.md` per
`knowledge-maturation-functor.md`, which then set N=3 as the threshold at which an
operator-memory pattern earns a canonical rule (N=2 since 2026-10-03).

## The instances

| # | Where | What the downgraded concern actually was |
|---|---|---|
| 1 | PR #5141 | a real defect, recorded in the origin memory |
| 2 | a race on #5393 | real — reproduced failing **5 of 5** runs |
| 3 | PR #5673, 2026-09-14 | `UNQUOTABLE` at `tests/test_pq_reexec.py:689`. Reading the cited region found a **false figure in the author's own comment**: it claimed narrowing `resolve()` saved ~21s and was "most of this test's runtime", when the saving was ~0.1s and the file's ~20s belonged to a detector the change never touched |
| 4 | PR #6252, 2026-09-19 | `UNQUOTABLE` at `lib/review_index.py:435`. The cited region held two defects: with no commit inside the window, `max(..., default=0) + 1` yielded **1**, dating uncommitted work to **1970-01-01** and sorting it to the bottom of an index built to surface recent work; testing that surfaced a duplicate-entry bug beside it |
| 5 | PR #6255, 2026-09-19 | `UNQUOTABLE` at `lib/review_index.py:136`. OSC escape sequences slipped past a CSI-only pattern, so stripping the bare ESC left the payload as **visible text** — a half-strip inside a function named for stripping control codes, where the residue reads as content |
| 6 | GRIP#6380, 2026-09-22, on a **MERGE** verdict | an assertion checked a dictionary the test had built three lines above, so it could never fail — inside a PR whose whole purpose was adding tests that can |
| 7 | GRIP#6379, 2026-09-22 | a docstring still said a cleanup is never called, in the same change that called it; the commit falsifying it was the author's own, two commits earlier |
| 8 | GRIP#6379, 2026-09-22 | described as a silent no-op, it was worse: `broly auto` under a forcing env var printed a success line and exited 0 while `status` still reported manual — a false success about a fan-out brake, in the PR meant to give the operator that brake |
| 9 | GRIP#6448, 2026-09-22, on a **MERGE** verdict | the documented verification recipe could not reproduce what the verifier computes; it agrees only on the one published example anyone runs, and reads a valid record as FORGED on any other |
| 10 | GRIP#6448 | "all 40 lines" — the file is 41 |
| 11 | GRIP#6448 | a usage docstring named two files that do not exist, copied from a scratchpad; a reader typing it gets file-not-found |
| 12 | GRIP#6449, 2026-09-22 | `subprocess.run(..., text=True)` decodes strictly and a git path is raw bytes, so a non-UTF-8 index entry raises `UnicodeDecodeError` — a `ValueError`, caught by neither `OSError` nor `SubprocessError`. It would escape the grounding check and fail the very review that guard exists to rescue |
| 13 | GRIP#6449 | an import sorted before `datetime` rather than after — cosmetic, and a mechanical failure wherever a lint gate covers the file |
| 14 | CodeTonight-SA.github.io#27, 2026-09-22, on a **MERGE** verdict | `QUOTE_NOT_FOUND` at `tools/happi-version.mjs:13` — the quoted text was **verbatim correct** and the line number was wrong by fourteen. It named two absolute paths carrying the operator's home directory, in a tool shipped to a **public** repo: `path-portability.md` and `operator-anonymity.md` breached at once, with the same file already merged in a sibling repo |

Instance 3 is the sharpest, because the author had **already measured the figure
as false** and corrected it in the PR description while leaving it standing in
the code — where the next reader has no PR description to consult. Nothing in
the test suite could catch it. Only reading the line did.

Note what AGORA got right there: it could not quote a source line, so its own
validity gate correctly refused to let it block. **It still pointed at the right
region.**

## Falsification

Currently refuted: **14 of 14** downgraded
concerns that have been R0-tested were real, across 2026-08-30 to 2026-09-22.

The retraction that number comes from is instance 6-8's sitting: three concerns
and three suggestions were tested the same night, and reporting "nine of nine"
merged the rows. Only 1 of those 3 suggestions was real, so the true pair was
8 of 8 and 1 of 3: "nine of nine" counted that one real suggestion as a ninth
concern and left out the two that were not.

The table above is the evidence for the concerns row, and it is deliberately
IN this file: the operator's memory ledger
(`feedback_agora_downgrade_is_form_not_substance.md`) carries the fuller
account, but it lives outside the repository and is gitignored, so a reader who
has only this checkout cannot open it. Citing it as the sole source would be
this rule's own failure — a claim whose evidence the reader cannot reach. It is
named as additional depth for whoever has that tree, never as the thing holding
these numbers up.

## Siblings

Origin memory: `memory/feedback_agora_downgrade_is_form_not_substance.md`.
