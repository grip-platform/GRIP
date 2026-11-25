# Congruent Continuity — Commitments Before Actions, Settled Exogenously (PARAMOUNT)

Origin: 2026-08-30, V>> directive (the /goal + the mid-session sharpening:
*"if we can accurately predict that the AI is on 'the wrong track' and either
course-correct if the error is obvious or HALT and then ask the user for
clarity, it will be a MASSIVE win"* — and the rename: Congruent Continuity).
Spec: `CONGRUENCE.md` (root polyglot sibling to `IDR.md`/`MEMORY_CONTEXT.md`).
Genesis + locked HITL decisions: `docs/congruent-continuity-genesis.md`.

## The law

> **On a decision-gating premise, pre-register a signed falsifiable
> commitment BEFORE the action executes** (claim + falsifier + deps +
> severity; `lib/continuity_chain.py::commit`). **Settle it exogenously** —
> deterministic probe floor first, non-Anthropic triage above it — never by
> the agent grading itself. **A refuted premise marks its dependency closure
> SUSPECT** (contamination), and the brake is tiered: steer when the
> correction is obvious, HALT + ask when ambiguous, hard-block only the
> irreversible class.

## Why (the load-bearing reason)

A wrong-track agent is *coherent with a false premise* and confident by
construction, so self-assessed confidence — the current HALT trigger — is the
least reliable signal GRIP has (prove-it doctrine, council seal `b32af619`).
The congruence verdict moves the HALT trigger to an exogenous measurement.
The signed append-before-act ordering is the anti-HARKing property: reasoning
cannot be retro-fitted to outcomes (cryptographic, not narrative, causation).

**Scope that claim precisely, because it is narrower than it reads.** Measured
2026-09-17 against a real leaf: the commitment's `ts` is **self-asserted by the
writer**, and the `audit` block carries `custody`, `ed25519`, `mldsa65`, `scheme`
and `tenant_id` — signatures, which prove WHO wrote a leaf and never WHEN.
Hash-chaining proves ordering *relative to other entries in the same chain*; you
cannot insert between existing leaves without rewriting everything after. It does
not prove ordering relative to the outside world, so nothing prevents appending a
commitment after the action with a `ts` that says otherwise.

So the property holds **against a third party who lacks the signing key**, and is
**not established against the writer** — who is the party whose honesty
pre-registration exists to constrain. Closing that gap needs an external time
anchor (OpenTimestamps, an RFC3161 TSA, or any third-party-verifiable
commitment), which is precisely why a non-Anthropic council named commit-reveal
schemes as prior art for this leg (seal `7dc01f0b739be243`). Until such an anchor
exists, say "signed and chain-ordered", never "provably before the fact".
Evidence: `docs/measurements/2026-09-17-adversarial-boundary-injections.md`.

## Mandate

- The recordable unit is a **falsifiable commitment**, never narrated
  chain-of-thought (locked decision D1 — self-narration is the confabulation
  class).
- Scope = **decision-gating premises** (R0 scope), not every thought:
  volume past that set is noise by the genesis falsification clause.
- Settlement evidence is **deterministic wherever a probe exists**
  (llm-augmentation-pattern floor); LLM checkers are non-Anthropic Pool-B,
  capability-locked read-only (untrusted-content-capability-lock).
- The pre-hoc watcher ships **WARN-only until its precision is measured** on
  the post-hoc corpus (locked decision D3); when a blocking tier arms for the
  irreversible class it MUST pass the escape-hatch audit
  (`gate-resolution-escape-hatch.md` Invariant 1) — the watcher must never
  block its own resolution.
- Steering keeps momentum; blocking is reserved for `severity=irreversible`
  (full-power-stop-override: keep the power, add a brake — never a blanket gate).

## Enforcement tier (knowledge-maturation-functor)

Rule-now (V>> PARAMOUNT directive; the moat's fourth leg). Mechanical floor:
the recorder + fail-closed settlement + contamination closure are LIVE
(`lib/continuity_chain.py`, `tests/test_continuity_chain.py`, including the
tamper-flips-to-BROKEN proof). The post-hoc settlement worker is BUILT
(2026-09-08): the deterministic probe floor (`lib/continuity_settle.py`, under
a per-tool read-only policy and an allowlisted environment — a first-token
allowlist was shown to let `git` and `rg` spawn arbitrary programs, and
`tests/test_continuity_settle_hardening.py` pins the closure), the L2
exogenous council leg (`lib/continuity_council.py`, injectable council
function; no live sitting recorded yet), the one-pass driver
(`lib/continuity_worker.py`) and two consumers of `contamination()`
(`suspect()` → the worker's contamination log and the watcher's check (d)).
A scheduled pass exists (2026-09-10): `bin/grip-continuity-settle` runs the
deterministic floor over every chain file, session-namespaced ones included,
and `config/launchd/com.grip.continuity-settle.plist.template` runs it daily
once installed on a machine (installation is a per-machine step; the template
header carries the command). The statistical predictors NOW HAVE a paired
corpus and a measurement: `lib/continuity_pairing.py` joins a label to the
transcript event that made the decision and extracts the vector AS-OF that
event; `lib/continuity_precision.py` computes cross-validated flag precision
with a Wilson interval; `scripts/continuity-precision-report.py` writes
`docs/measurements/2026-09-10-congruent-continuity-precision.md`. First run,
2026-09-10, seed 0, k=5 stratified folds, feature window 50 events, at tree
`0cac6f7f`: 76 paired rows (70 git proxies, 6 chain verdicts) out of 4,778
labels, paired base rate 13.2%, and NO predictor's precision interval clears that base rate —
the honest reading is that precision is measured and insufficient, so the
watcher stays WARN-only on evidence. The self-assessed-confidence baseline
the falsification clause names is UNMEASURED: no commitment record carries the
agent's confidence. A record-only recorder (`hooks/continuity-push-recorder.py`,
bound on PreToolUse Bash in the production profile) pre-registers the
level-with-main premise before every `git push`, with the transcript path
that makes the new record pairable, so the corpus grows without a human in the
loop. The PRECOG watcher itself remains a WARN-only skeleton whose hook is
UNBOUND — this rule claims no live gate until D3's precision bar is met; that
PreToolUse binding is the V>>-gated activation step, mirroring `no-stubs.md`'s
posture. (Genesis numbering: W3 = settlement
worker, W4 = watcher; PR #5170's "W3-W5" title named the statistical
substrate — describe the work by component, not by wave.)

## Falsification

Wrong if: watcher precision proves no better than the self-assessed-confidence
baseline over ~20 sessions (drop the watcher, keep the chain as audit record);
OR commit volume beyond the decision-gating set makes the chain noise (narrow
the scope, never widen it); OR no contamination set is ever consumed by a
replay/RCA (the analysis surface is dead — wire it into RSI or demote); OR the
tiered brake's steer-injections are ignored or wrong more often than they
correct (D2's falsifier — demote steer to warn). Track: prevented
verified-wrong actions per month (target > 0 once armed), divergence-to-
detection distance (shrinking), settlement coverage (unsettled backlog → 0),
token overhead (< 10% per H-CC-3).

## Scope

Every session and autonomous loop once the watcher arms; the recorder is
available to all surfaces now. Sits beside `cryptographic-causation.md`
(legs 1-3), `r0-critical-thinking.md` (the discipline this mechanises),
`prove-it-claims-canonical.md` (self-reports are the highest-risk claims),
`exogenous-anchor-law.md` (the verifier is never the author),
`verify-canonical-not-claimed.md` (evidence = probes, not labels), and
`full-power-stop-override.md` (brake, never blanket gate). Mirror into
`~/.hal/rules/` when a HAL callsite earns it.

## How it is the fourth leg of Cryptographic Causation

The three legs in `rules/cryptographic-causation.md:12-31` — the IDR forest
(DECISION), the context chain (BELIEF), /prove-it (CLAIM) — record causation
at the endpoints. Congruent Continuity records the premises adopted BETWEEN a
belief and the decision it licenses, and it qualifies as a leg of the same moat
because it carries the same three properties, each anchored in code:

- **Commitments before actions** — the commit is signed and chain-linked
  before the outcome is known (`lib/continuity_chain.py:146`), so the
  reasoning cannot be retro-fitted to the result: cryptographic, not narrative,
  precedence (`docs/congruent-continuity-genesis.md:79`, D1). Read this with the
  scope named above: chain-ordered and signed, which binds a third party without
  the key, and NOT anchored to an external clock, which is what would bind the
  writer.
- **Settled exogenously** — a deterministic probe floor
  (`lib/continuity_settle.py:185`) and, above it, a non-Anthropic council whose
  verdict is the disagreement structure of the sitting votes and whose
  verifier is the model that judged (`lib/continuity_council.py:114,165`);
  under quorum is not a verdict; a dangling verdict raises
  (`lib/continuity_chain.py:201`). The verifier is never the author
  (`rules/exogenous-anchor-law.md:17-24`).
- **Contamination closure** — a refutation propagates along the recorded
  dependency edges (`lib/continuity_chain.py:285,300`) and is consumed by the
  worker and the watcher (`lib/continuity_worker.py:59`,
  `lib/continuity_watch.py:176`), so the blast radius of a wrong premise is
  computed, not guessed.

Zero new cryptography: the records are PrecogIDR envelopes under the same HMAC
signing and forest verification (`lib/precog/idr.py:311`,
`lib/idr_forest.py:194,314`), rooted at an admissible human anchor
(`lib/continuity_chain.py:75`). The full anchored account, with the honesty
clause that no live council sitting is yet recorded, is the section "The
fourth leg of Cryptographic Causation" in `CONGRUENCE.md`.
