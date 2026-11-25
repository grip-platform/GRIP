---
name: onboarding-interview
description: Conversational HITL interview that gets to know a NEW operator beyond the fixed activate-grip-tui steps — their real work, first goal, comms register, audience, and autonomy appetite — then uses the answers to set GRIP up optimally (surface the right skills, stack the right modes, pick the output tier) and records the whole setup as a signed, tamper-evident IDR so every choice is provable ("GRIP set you up this way BECAUSE you said X"). Ends on a signed Operator Profile Card. Use after /activate-grip's fixed steps complete, when .onboarded.json is present, or when the operator says "get to know me", "interview me", "tune GRIP to how I work", "set GRIP up for me". Composes activate-grip-tui + asking-users + creating-wizards + hitl-decision-matrix + auto-mode + surfacing-skills; reimplements none of them.
---

# Onboarding Interview

The fixed `activate-grip-tui` wizard captures *identity* (name, signature, tier,
model, output style). This skill captures *fit* — how the operator actually
works — and turns it into a GRIP that is tuned on day one, with a **receipt for
every setup decision**.

It is a thin **orchestrator** over a pure, tested core (`lib/onboarding_interview.py`,
67 Goodhart-proof tests). The session runs the flow; the lib holds the logic.

## When it fires

- Right after `activate-grip-tui` finishes its fixed steps (composes, never replaces).
- Operator asks to be "interviewed" / "get to know me" / "tune GRIP to my work".
- Never re-asks a fixed step: read `.onboarded.json` first and skip anything already set.

## The flow (the session IS the runner)

### 1. Ask — five cold seeds, then persona branches (HITL, plain-language)

Deliver the **five seed questions** as ONE batched decision set via
`hitl-decision-matrix` (ELI5 framing, one confirmation), never five separate prompts:

| id | question | sets |
|----|----------|------|
| `primary_work` | "What will you mostly point GRIP at, day to day?" | persona pivot + default mode + skill re-rank |
| `first_goal` | "What's the first real thing you'd want GRIP to help you get done? (your words)" | free-text → `auto-mode` mode stack + `/ultragoal` brief + welcome-memory + card headline |
| `comms_register` | "How do you like answers delivered?" | `output_tier.set_tier` (verbosity only) |
| `audience` | "Who reads what GRIP produces?" | tone + safety posture (authentic-writing / redaction / no-shilling) |
| `autonomy` | "How much should GRIP do before checking with you?" | discretionary-gate posture (never the HALT classes) |

Then reveal **only the matching persona branch** (progressive disclosure via
`creating-wizards`) — `dev_stack`/`dev_rigour` for builders, `founder_front` for
company-runners, `design_surface` for designers, `writer_focus` for writers. An
operator never sees another branch's follow-ups. Any operator's answered path is
**≤7 questions (≤8 for a team)**.

Free-text on `primary_work` ("a mix") is handed to `auto-mode`'s `select_modes`
to infer — not forced into a speculative fifth persona (YAGNI). Contradictions
between answers are resolved with a single `asking-users` clarification before
anything is persisted.

### 2. Map — pure decision engine

```python
from onboarding_interview import map_answers_to_config, QUESTION_BANK
actions = map_answers_to_config(answers, catalog)   # -> list[ConfigAction]
```

`map_answers_to_config` is **pure and deterministic** (no I/O — setters and the
skill catalog are injected). Each `ConfigAction` carries `question_id`, `answer`,
`op`, `args`, and `because` (the plain-language reason, which becomes the card's
provenance line). Every actionable answer yields **≥2 config actions** — a
question that cannot is mechanically rejected as not earning its place.

### 3. Execute — via the REAL setters (add-only invariant)

Run each `ConfigAction` through its existing GRIP surface — the interview
**composes, never reimplements**:

- `output_tier` → `lib.output_tier.set_tier`
- `surface_skill` → note in the session + `surfacing-skills`/`check-reachable`
- `stack_mode` → `auto-mode`
- `identity`/`persona` → `lib.activate_grip_state.write_identity` / `write_onboarded`

**Add-only invariant (hard):** the interview NEVER calls `grip_model.set_model`
or `grip_tier.set_tier` — the plan/context-envelope and the model are the
operator's explicit `activate-grip-tui` choices and are out of bounds here
(a `test_never_emits_forbidden_plan_tier_or_model_ops` guards this).

### 4. Seal — the moat dogfood (provable setup)

Build the IDR payload and sign it into a dedicated onboarding sub-forest:

```python
from onboarding_interview import build_interview_idr
from lib.precog.idr import content_addr, build_idr, append_idr   # real signer

payload = build_interview_idr(answers, actions, addr_fn=content_addr)
# one DECISION leaf per (question→answer→config_action) + one cross-linked BELIEF
# node per leaf, rooted at the operator's exogenous "human:onboarding:<fp>" anchor.
```

Sign each leaf via the real `build_idr`/`append_idr` into
`state/precog/onboarding-idr.jsonl` (a flock-atomic ledger kept separate from the
main forest). The result is tamper-evident and replayable: anyone can later verify
`verify_chain_integrity` and prove GRIP configured the operator **because** of a
specific answer. This is `cryptographic-causation` dogfooded on the operator's
very first interaction.

### 5. Delight — the signed Operator Profile Card

```python
from onboarding_interview import render_profile_card
print(render_profile_card(profile, verify_fn=<forest verifier>))
```

A plain-Unicode card (no ANSI — chunk-safe) reflecting the operator back to
themselves *with receipts*: signature as AUTHOR of the setup, inferred persona,
chosen register, live goal headline, mode stack + top-3 surfaced skills, a
**per-line provenance trail**, and a GRASP Merkle-root footer.

**Fail-closed (hard invariant):** if the seal does not verify, the WHOLE card is
withheld — a delight that lies about its own provenance is worse than none. Any
individual line whose provenance is untraceable renders `[NOT-FOUND]`.

## Design invariants (tested)

- **Pure core / DIP** — `map_answers_to_config`, `build_interview_idr`,
  `render_profile_card` do zero I/O; every side-effecting dependency is injected.
- **Add-only** — never mutates plan-tier or model.
- **Minimal** — every question earns its place (≥2 config actions per answer).
- **Fail-closed delight** — card withheld on a bad seal.
- **Deterministic** — same answers → byte-identical payload (replayable).

## Follow-ups (honest scope — not yet covered by tests)

- **Skill-reachability guard** (`test_surfaced_skills_and_modes_are_real`): assert
  every skill/mode the mapper surfaces resolves to a real `skills/*/SKILL.md` — the
  confab defence that stops surfacing a non-existent skill.
- **Impure round-trip test**: sign a real payload via `idr_forest`, assert
  `verify_chain_integrity` is intact, then tamper one leaf and assert it goes
  BROKEN — proves the moat end-to-end against the real signer.

Both are tracked for the AGORA convergence pass; the pure core + 67 tests ship first.

## Hypothesis

**H-ONBOARD-INT-1** — a ≤7-question interview that maps to config + a signed IDR
raises day-one setup quality (right skills/modes/register surfaced) without adding
friction. Falsified if the median interview exceeds ~2 min, if operators report
the surfaced setup as wrong more than they accept it, or if the map produces a
contradictory config on any real answer-set. Verification: first 10 real runs.

## Related

`activate-grip-tui` (fixed steps it composes) · `asking-users` /
`creating-wizards` / `hitl-decision-matrix` / `hitl` (the HITL substrate and its dispatch contract) · `auto-mode`
(mode inference) · `surfacing-skills` / `check-reachable` (skill discovery) ·
`cryptographic-causation` + `lib/precog/idr.py` + `lib/idr_forest.py` (the moat) ·
`prove-it` (per-line provenance).
