---
paths:
  - "**/*.py"
  - "bin/**"
---

# Completeness — No Scrubs (a stub IS a scrub) (PARAMOUNT)

Origin: V>> directive 2026-06-08 — *"I don't want no stubs ... a sense of COMPLETENESS —
CORRECT-BY-CONSTRUCTION — a complete NO-GO in our CBC power to write stubs. Because the
stubs drift, and later the AI starts to think it is implemented ... confabulating ... they
shouldn't exist at all."* Ratified by a grounded broly council + 3 adversaries
(`drafts/btw-idr-decision-anatomy-fork-output.md` lineage; council `wf_610c8a6e`). The 9th
design principle, peer to SOLID / GRASP / DRY / KISS / YAGNI / YSH / CC / BIG-O.

## The principle

**Completeness is the integrity-axis: given that you ARE writing a symbol, you write it
FULLY — never a hollow placeholder that names a capability the code does not provide.**

It is the **orthogonal complement of YAGNI, not a fold-in of it**:

- **YAGNI** = the *need* axis — *whether* to build a symbol.
- **Completeness** = the *integrity* axis — *whether an existing symbol is honestly whole*.

They are two axes, not two halves of one cut. Proof they are independent:
`lib/idr_forest.py::forest_edit_distance` is *needed-now* (a live xfail consumer depends on
it) — so YAGNI is silent; only Completeness catches it. (That is the exact instance V>>
flagged.)

**The decision matrix has three branches, and the stub is the forbidden one:**

| Need? | Do |
|---|---|
| No known need | write nothing **and** file nothing |
| Future need, not needed now | write **NO code** — file a **tracked issue** |
| Needed now | **implement it FULLY** |

The **scrub** is future-need recorded as **dead code instead of an issue**. The correct fix
for un-needed deferred work is **DELETE + file an issue**, *never* implement-on-the-spot
(that would invert YAGNI and force speculative code behind every seam).

## Why stubs are uniquely corrosive (the load-bearing why)

A stub emits a **FALSE COMPLETENESS SIGNAL**: a later session reads the wired-looking
symbol, assumes it is implemented, builds on it, and **confabulates**. *A loud-failing
stub is a quieter lie, not the truth* — `raise NotImplementedError("… not wired yet")` is a
scrub exactly as much as a silent `pass`, because the harm is the **signal to a future
reader**, not the runtime behaviour. (Secondary aggravator: a stub is an unclosed-lifecycle
record — no issue, no deadline, no reaper — `lifecycle-closed-or-expired.md`. But the
load-bearing justification is the false-completeness signal, not lifecycle-accretion.)

Discriminator — **meaning, not tokens**: *"never / unsupported / by-design"* = COMPLETE;
*"not yet / wired later / Phase N / skeleton / P2-P3"* = SCRUB.

## What is NOT a stub — the carve-out table (LOAD-BEARING SAFETY, as prominent as the prohibition)

Over-applying this principle is itself a failure (deleting a real test mock; refusing a
legit `@abstractmethod`; turning an honest `return []` into fake-wired code or a
caller-breaking raise). These are **complete by construction and MUST NOT be flagged**:

- `@abstractmethod` / `@abc.abstractmethod` / `@abstractproperty` / `@overload` bodies — the body IS the override contract.
- Methods on a `Protocol` / `ABC`-based class — signatures by language spec.
- `.pyi` type-stub files — the whole file is stubs by definition.
- Test doubles / fakes / mocks **inside test paths only** (`tests/**`, `test_*.py`, `*_test.py`, `conftest.py`, `*fake*`, `*_mock*`) — never a hollow production-default behind an env flag.
- **Intentionally-unsupported-FOREVER** ops — `raise NotImplementedError("read-only mapping; mutation unsupported by design")`: a COMPLETE implementation of "this operation does not exist by design".
- **Runtime-dependency boundary** raises — `"requires grip-channel MCP or SLACK_BOT_TOKEN"`: documents a *present* dependency the caller must satisfy, not deferred wiring.
- **Defensive dispatch-fallthrough / exhaustive-enum-miss** where every current case IS handled (the raise only catches a future-added value).
- **Empty no-op override** — a bare `pass`/`...` overriding a concrete base method (a deliberate no-op IS the complete impl). Empty bodies name no missing capability.
- **Honest empty value** — `return None/[]/{}/""` even with a deferral COMMENT (`backlog/reducer` is the canonical GOOD pattern). The value is complete; the comment is honest.
- **In-body raise inside otherwise-live logic** — out of scope for the definition-scoped mechanical gate (covered by review; see falsifier).

Discriminator (the AST detector encodes this): only a **raise** or a **fake-`return`** that
*names a capability it does not provide* is a hard scrub. An empty `pass`/`...` is at most a
soft "suspect" (review, never hard-DENY).

## Enforcement (knowledge-maturation-functor)

- **Rule-NOW** — V>> declared it PARAMOUNT; it is also N≥2 *emergent* in our own tree before
  being named (`backlog/reducer.py` self-justifies no-faked-coverage; `idr_forest.py`
  self-justifies the no-silent-stub stance via Exogenous-Anchor-Law).
- **Mechanical floor (L1):** `lib/no_stubs_detector.py` — pure AST, zero-dep, mockable.
  Definition-scoped; drift>anti precedence; `pass`/`...` need a drift marker; return-stubs
  caught via drift in the returned content (closes the `raise`→`return` laundering attractor).
  Goodhart-anchored corpus: `tests/test_no_stubs_detector.py` (23 cases — real in-tree scrubs
  DENY, the legit carve-out population ALLOW, mutation sensitivity).
- **Gate (L2):** `hooks/no-stubs-gate.py` — PreToolUse `Write|Edit`, introduced-vs-pre-existing
  partition (mirrors `guardrail-evasion-gate.py`), audit to `logs/no-stubs-gate.jsonl`.
  Ships **WARN-mode**; introduced+drift scrubs promote to **DENY** only after the corpus test
  is green AND a WARN-window false-positive rate <10% on the known-legit population. NOT
  yolo-relaxable for introduced scrubs (an autonomous loop under time pressure is the most
  likely actor to emit "P2 wires this").
  **Wired and live in WARN mode.** `settings.json` binds `hooks/no-stubs-gate.py`
  on PreToolUse `Edit|Write`, and it audits to `logs/no-stubs-gate.jsonl`. Be exact
  about what that log proves: it shows the hook EXECUTES, not that it has caught a
  real edit. The promotion to DENY remains the V>>-gated step, pending a WARN-window
  false-positive rate under 10%. (This paragraph said the gate was unwired until
  2026-08-31 - a PARAMOUNT rule understating its own enforcement.)

## Remediation of existing debt (two-branch, NEVER blanket-implement)

21+ in-tree scrubs (scan 2026-06-08). For each: needed-now → wire fully; not-needed-now →
DELETE + file ONE tracked issue. Tracked as a follow-up to #2148. Examples:
`idr_forest.forest_edit_distance` (V>>'s flagged instance — likely delete+xfail-remove+issue,
the xfail being a deferral marker, not a live consumer); `broly_council` default branch (wire
via `hal_llm_adapter` — dep exists); `comms_update._StubAdapter` family (delete 4);
`whatsapp.py` / `integrations.py` return-stubs / `parent_context_loader` (delete-or-wire);
`hal/router` (reword "not yet implemented" → "unsupported strategy" — cosmetic, legit).

## Falsification

Wrong / mis-tuned if, over the next ≥50 hollow-body Write/Edits it governs: (a) false-positive
rate on the legit population (abstractmethod/Protocol/template-hook/intentionally-unsupported/
runtime-dep/honest-empty) exceeds 10% → tighten the carve-out, NEVER drop the gate; (b) it
never once fires on a real "not-wired" stub over 50 dispatches while measurably slowing legit
work → downgrade to WARN+audit; (c) a real stub still reaches a commit via a body shape the
matcher misses → widen the matcher (the in-body-mid-function case is a known accepted gap —
covered by review; escalate to a narrow detector only at ≥2 traced confabulation incidents);
(d) it forces ≥3 YAGNI/DRY/SOLID violations (speculative far-side code) to satisfy it → WARN
not DENY + sharpen "hollow-present, not missing"; (e) every confabulation it would prevent is
also prevented by Rule 18 (JIT-CBC) alone → demote to a Rule-18 clause, not a 9th principle.
Track {FP rate, sibling-violations-forced, gate-deadlocks (target 0), confabulations-uniquely-
prevented} via `logs/no-stubs-gate.jsonl` → /evo. Goodhart-safe: success = confabulations
prevented / scrubs-kept-out, never gate-fire-count.

## Scope

Every `Write`/`Edit` of Python in every repo once wired (Python-AST-only at N=1; JS/TS defers
to a per-language shim — hard-coding a JS regex would reintroduce the substring unsoundness the
AST exists to kill). Sits beside `cc-enforcement.md`, `efficiency-rules.md` Rule 18 (JIT-CBC —
this is its existence-honesty FACET, cross-referenced not merged), `no-guardrail-evasion.md`
(the gate template), `knowledge-maturation-functor.md` (rule-now / shim-at-N=2),
`lifecycle-closed-or-expired.md` (the secondary aggravator). Mirror into `~/.hal/rules/` when a
HAL callsite earns it.
