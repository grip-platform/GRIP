# Never Run GRIP Code Under the System Python: annex

The narrative behind `rules/never-system-python-for-grip.md`: its origin, incidents and measurements. The rule holds the operative text; nothing here loads at startup.

Origin: 2026-09-13. `python3 ~/.claude/lib/grip_save.py` was typed by hand. GRIP
did not fail. It **silently downgraded** the IDR signature to a shared-key HMAC
whose own docstring calls it *"Forgeable by design"*, printed one warning above a
wall of green ticks, and reported `12 passed, 0 failed`. The mistake was then
reported to V>> as a GRIP failure. His correction is the reason this is a rule
and not a note: *this has happened before, and it has been fixed.*

He was right, and that is what makes it worth writing down. **The fix already
existed.**

It was wired into six tools in
`bin/`. `lib/grip_save.py` was not one of them — and neither were six other
`lib/` modules, nor `bin/grip-continuity-variance`.

## What made it recur, and the part that stings

`tests/test_pq_reexec.py` is the forward anchor written *specifically* to stop
this recurring. Its docstring promised a check derived from the import graph
*"rather than from a list somebody has to remember to update."* It then walked
one directory — `bin/` — which is a hand-maintained list of length one.

`lib/grip_save.py` carries the `#!/usr/bin/env python3` shebang and reaches
`append_idr` at depth 1 through `context_chain.py`. It was invisible to the test
built to catch exactly it. That is `tests-must-be-able-to-fail.md` shape 1 —
green while the thing it guards is broken — and it was **proved** before it was
fixed: with `lib/grip_save.py` genuinely un-wired, the bin-only walk passed green
and the widened walk failed red naming the file.

The anchor was also **already red on `origin/main`**, catching
`bin/grip-continuity-variance`, which shipped in GRIP#5600 and was edited again
in GRIP#5605 without anyone noticing. At the time, no workflow ran that test
file at all — the separate defect filed as GRIP#5630, *a forward anchor bound
to no CI gate is inert*. Two independent guards, both present, both blind.

**GRIP#5630 is FIXED, and this paragraph said otherwise for a day.**

Stated in
the past tense above because a rule asserting a repaired defect as live is the
same doc-rot this file exists to catch, and because the stale sentence was
relayed onward three times on 2026-09-14 before anyone opened the workflow.

## Mandate

[The two checks have
   different teeth on purpose: a path check is not a capability check] (Codex
   raised exactly that on GRIP#5640, and it was right)[, but refusing on
   capability would block `grip-py -m pip install cryptography`, which is the
   remedy.]

When this rule was
   written `requirements.txt` did **not** carry it — a core capability with an
   undeclared dependency, filed as GRIP#5646 — so "install from requirements"
   would have handed the operator a venv the shim accepts and which still cannot
   sign.

Stated in the past tense for the same reason as the GRIP#5630 paragraph below:
   a rule asserting a repaired defect as live is the doc-rot this file exists to
   catch, and here it did worse than mislead — it steered the reader away from the
   command that now actually works. Verified by reading `requirements.txt:79` and
   `gh issue view 5646`, not by assuming the fix followed the filing.

Corrected 2026-09-14 against
   the CHANGELOG rather than against a peer's report — the same external-fact
   discipline `prove-it-claims-canonical.md` names, applied to a number this
   rule had asserted from a relayed claim.

[Both are cross-platform and both already exist] —
   five committed callers were spawning bare `python3` while `grip_closeout.py`
   resolved `GRIP_PYTHON` correctly at six *other* call sites in the same file[.]

[A `#!/usr/bin/env python3` line on a non-executable module claims it is runnable]
and is what put `lib/sovereign_council.py` and `lib/prove_it_council.py` in
   front of the anchor. Running them writes nothing; the claim was false[.]

## Enforcement tier (knowledge-maturation-functor)

The widened walk
found eight unwired record-writing entry points, of which four import or call
`append_idr` directly.

[A gate able to refuse a `python3`
invocation could refuse the command that repairs a broken venv —
`gate-resolution-escape-hatch.md` Invariant 1], and a deadlock GRIP already hit on
2026-06-08 where only an external human could break it[.]

## Falsification

[Track: records written with `custody: demo-shared-key` whose downgrade reason is
`crypto-backend-unavailable` (target 0]; was ≥1 on 2026-09-13[), and record-writing
entry points shipping unwired (target 0 — now mechanically caught in both `bin/`
and `lib/`, though only where something actually runs that test file).]
