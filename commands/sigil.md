# /sigil — Earned proof-of-work sigils

Collectible glyph-stamps minted **only** for verified milestones — a merged PR,
a CONFIRMED hypothesis, a CI-green ship, a converged sprint. Earned, never
given: each sigil is generated purely from the milestone's own bytes
(`kind|ref|gen|ts` via SHA-256), so it cannot be forged without doing the work.
The sigil **is** the proof-of-work, made beautiful. Coheres with
`rules/exogenous-anchor-law.md` — delight rides a verified exogenous signal,
never a self-claim. Sigils accumulate in a browsable passport.

## Usage

- `/sigil` or `/sigil show` — render your passport (earned sigils + rarity tally)
- preview without recording: `python3 lib/sigil.py preview --kind pr --ref 412`
- mint (normally called by a milestone path):
  `python3 lib/sigil.py mint --kind <pr|hypothesis|ship|sprint> --ref <PR#/SHA/id> --gen $GRIP_GEN`

Backed by `lib/sigil.py` (pure-stdlib, SHA-256-deterministic, Goodhart-tested in
`tests/test_sigil.py`). Passport: `state/sigils/passport.jsonl` — append-only,
de-duplicated by `(kind, ref)`.

## Rarity

Deterministic from the milestone hash: `common` 60% · `uncommon` 25% ·
`rare` 11% · `legendary` 4%. You cannot reroll — the work picks the rarity.

## Mint triggers (earned-only)

Call `lib.sigil.mint_for_milestone(kind, ref, gen=..., verify=...)` (or the CLI)
from a verified exogenous anchor:

- PR merged (AGORA-green) · hypothesis CONFIRMED at deadline · CI-green ship ·
  sprint converged.

Earned-only is **enforced, not just asserted**: pass `verify=` — a checker the
trigger owns (e.g. `lambda k, r: gh_pr_is_merged(r)`). A fabricated ref (a PR
that never merged) is refused at the call site before anything is recorded. The
pure generator (`mint`) stays offline + testable; the `gh` dependency lives in
the trigger, never in `lib/sigil.py`. Never mint on a self-claim or a bare
`/save` — that would break the earned invariant the whole feature rests on.
