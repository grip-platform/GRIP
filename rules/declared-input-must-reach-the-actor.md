---
paths:
  - "lib/**/*.py"
  - "bin/**"
  - "scripts/**/*.py"
  - "hooks/**/*.py"
---

# Declared Input Must Reach the Actor (PARAMOUNT)

Origin: 2026-08-17, ULTRAPROMPT-hal-wiring-perimeter §5. Rule-at-N=4 with the
mechanical shim shipped alongside, per `knowledge-maturation-functor.md` (N=3
threshold cleared). The four instances, each verified before this rule existed:

| # | Surface | The dropped input | Fixed |
|---|---|---|---|
| 1 | `/ultrareason` council escalation | the QUESTION — the council was asked the literal word "council" (`lib/ultrareason.py` argv) | pre-2026-08-17 |
| 2 | `/ultralearn` | the operator's SUBJECT — `main()` read no argv at all while the `/ultralearn` skill surface accepted one | `8b88cf6f` |
| 3 | `/ultrajudge` | the QUESTION — it reached `_dispatch_stub`'s signature and died in its body; verdicts came from a constant table; two different questions returned byte-identical PROCEED-90% bodies | `abc5e3a8` |
| 4 | `/ultraread --council` | the PROFILE — declared at `:246`, read by nothing; `--council TOTAL-NONSENSE-PROFILE` was accepted silently with no roster change | `f9a4688c` |

## The law

> **An input a surface declares — a CLI flag, an operator argument, a
> dispatcher parameter — must reach the code that acts on it. An input that is
> accepted and silently dropped is forbidden: it makes the artefact look like
> it answered the caller while containing something the caller never
> influenced.** Either wire the input to its actor, or remove the declaration
> so passing it fails loudly.

This is the input-side twin of `no-stubs.md`: a stub names a capability the
code does not provide; a dead input names an *influence* the caller does not
have. Both emit a false signal a later reader builds on.

## The shim — `lib/wiring_perimeter_detector.py`

Pure AST, three statically detectable shapes:

- **`dead-cli-flag`** — an argparse dest carrying a VALUE that no code reads as
  `args.<dest>`. Abstains on every indirection (`vars`, `getattr`,
  `__dict__`, bare `args` passthrough, subparsers, `parse_known_args`).
- **`argv-blind-cli`** — a module with a `__main__` entrypoint that reads no
  CLI input at all (no `sys.argv`, no argparse/click/typer) while a same-named
  operator surface (`skills/<stem>/SKILL.md` or `commands/<stem>.md`) exists —
  `/<stem> anything` is silently dropped.
- **`dead-dispatcher-no-live-path`** — a `*dispatch*` function with a
  parameter its body never reads, in a module importing NO adapter, HTTP
  client or subprocess anywhere (function-level imports included).

Carve-outs (each pinned by a test, each from a measured legitimate instance):
dead **boolean toggles** (`store_true`/`store_false`/`count` — no operator
data to drop; the measured population's were documented no-ops);
**signature-only bodies** (`...`/`pass`/docstring — Protocol/ABC contracts,
the `no-stubs.md` carve-out); a labelled stub's dead params **beside a live
dispatcher** (the sanctioned fallback shape, e.g. `lib/ultraread.py`);
argparse `action="help"/"version"` (argparse consumes those itself).

## Calibration (2026-08-17, tests pin all of it against the real git objects)

- FLAGS pre-fix ultralearn (`git show 8b88cf6f^:lib/ultralearn.py`) ✓
- CLEARS post-fix ultralearn (`8b88cf6f` — reads `sys.argv:321`) ✓
- FLAGS pre-wiring ultrajudge (`a8f5927f` — `_dispatch_stub.question`) ✓
- FLAGS pre-removal ultraread `--council` and ONLY `--council` (`a8f5927f`) ✓

**Measured false-positive rate** over the full `lib/` population (1,028
files): raw detector 6 findings → 3 adjudicated FP (2 documented no-op
booleans, 1 Protocol signature) → carve-outs added → 2 findings, being 1 true
positive (`session_analytics` dead `command` positional, fixed same day) and
1 marginal (`gitgo` argv-blind with a real `/gitgo` surface, made loud same
day). Post-fix population: **0 findings / 1,028 files**.

## Enforcement tier

- **L1 shim (live)**: `lib/wiring_perimeter_detector.py` — importable +
  CLI (`python3 -m lib.wiring_perimeter_detector <files> --repo-root ~/.claude`).
- **L2 gate (shipped, INERT until bound)**: `hooks/wiring-perimeter-gate.py` —
  PreToolUse Write|Edit, WARN-mode (stderr + `logs/wiring-perimeter-gate.jsonl`
  audit), introduced-vs-pre-existing partition, fail-open, self-exempt.
  DENY only under `GRIP_WIRING_PERIMETER_ENFORCE=deny`.
- **The PARAMOUNT header names the PRINCIPLE's authority (review-tier,
  binding on every session now); the gate's WARN default is not a softening
  but the deliberate no-stubs.md calibration posture** — DENY is the earned
  promotion once the WARN window holds under the 10% FP threshold. Stated
  here explicitly so the pairing reads as designed, not contradictory
  (AGORA #4774 concern 1).
- **Activation is the V>>-gated step** (shared config, no-stubs.md precedent):
  add a binding in `config/hooks.registry.json` alongside
  `policy.no-stubs-gate-py`'s PreToolUse binding, pointing at
  `hooks/wiring-perimeter-gate.py`. Until then the gate has zero blast radius.
- Escape-hatch audit (`gate-resolution-escape-hatch.md` Invariant 1): WARN
  mode blocks nothing; in DENY mode the denied set is Write/Edit of the
  offending file, and the resolution (wire the input or delete the
  declaration) is itself a Write/Edit that removes the finding — the gate's
  introduced-only partition plus the before/after diff makes that edit pass,
  so the gate cannot deadlock its own resolution.

## Falsification

Wrong if, over the next ~50 flagged edits: the WARN false-positive rate on
legitimate patterns exceeds the 10% no-stubs threshold (tighten carve-outs,
never drop the gate); OR a real silently-dropped-input defect ships that none
of the three shapes could have caught (widen the shapes — the known accepted
gaps are dead boolean toggles and semantic constants-behind-read-params, the
latter covered by the provenance-sweep discipline instead); OR the detector
never fires on a real instance in 50 dispatches while slowing work (demote to
CI-only). Track: findings per quarter that survive adjudication (target: every
new one fixed same-week) and `logs/wiring-perimeter-gate.jsonl` FP rate.

## Scope

Every Python surface that declares operator/caller input: `lib/`, `bin/`,
`scripts/`, `hooks/`. Sits beside `no-stubs.md` (the output-side twin),
`tests-must-be-able-to-fail.md` (the green-suite blindness that let #3 live —
`test_role_steering_visible` never varied the question),
`verify-canonical-not-claimed.md` (a keyword scan scoring pre-fix ultralearn
CLEAN is why the detector traces data paths, not keywords), and
`knowledge-maturation-functor.md` (the N=4 promotion). Mirror into
`~/.hal/rules/` when a HAL callsite earns it.
