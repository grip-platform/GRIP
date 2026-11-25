# /activate-grip

TUI++ activation wizard. Three-act onboarding (Identity → Capability → First Run)
that preserves the legacy wizard's filesystem state contract while delivering
a delight-first first-touch experience.

## Usage

```bash
/activate-grip           # Run full TUI++ wizard
/activate-grip --status  # Check current auth + onboarding status
/activate-grip --reset   # Reset identity and re-run from scratch
```

## What It Does

The slash command resolves to `skills/activate-grip-tui/SKILL.md` (issue #2520
TUI++ replacement, shipped 2026-05-04). The legacy procedural wizard at
`skills/_archived/authenticating-with-claude/SKILL.md` is archived (GRIP #3139,
V>> override of the original H682 90-day defer). The override is safe *because*
both writers emit `.onboarded.json` v6.0.0 with the same nine `steps_completed`
— downstream consumers (upgrade detection, settings regeneration,
hooks/session-start) cannot tell which wizard ran, so retiring the legacy
wizard early carries no downstream-parity risk.

## Three Acts

```text
Act I — Identity      Auth → Name → Persona → Signature → Signature reveal
Act II — Capability   Mode → Extended Thinking → Budget Mode → Confirm
Act III — First Run   Save → Persona toolkit install → Welcome memory → Semantic memory (opt-in) → /coffee demo
```

## First-Run Behaviour

If `.onboarded.json` is missing on session start, `/activate-grip` is auto-invoked
by the session-start hook. After the wizard completes, `.onboarded.json`,
`facts/identity.md`, and `settings.local.json` are all created atomically.

## Schema Parity

| Artefact | Schema | Source of truth |
|----------|--------|-----------------|
| `.onboarded.json` | v6.0.0 | `lib/activate_grip_state.write_onboarded` |
| `facts/identity.md` | name + persona + signature + mode + extended_thinking + created | `lib/activate_grip_state.write_identity` |
| `settings.local.json` | reference template + GRIP_OPERATOR + GRIP_BUDGET_MODE | jq the reference template (legacy parity) |

## Integration

- `@asking-users` — PARAMOUNT source of truth for AskUserQuestion
- `@creating-wizards` — anti-patterns and bidirectional pattern
- `@hitl` -- the dispatch contract that chooses between `@asking-users` and `@creating-wizards`
- `@swiss-nihilism-design` — aesthetic reference for the pre-context panels
- `lib/activate_grip_signature.py` — signature reveal renderer (no ANSI escapes)
- `lib/activate_grip_welcome.py` — welcome memory entry generator
- `lib/activate_grip_state.py` — `.onboarded.json` + `identity.md` writers (legacy parity)
- `lib/embeddings_warm.py` + `lib/embeddings_warm_wizard.py` — opt-in always-warm semantic memory (Act III step; `grip embeddings-warm` to revisit, `/verify-embeddings` to prove save+retrieve). Never blocks activation if ollama is absent.

## Related

- `skills/activate-grip-tui/SKILL.md` — the wizard itself
- `skills/_archived/authenticating-with-claude/SKILL.md` — archived legacy wizard (superseded by /activate-grip-tui, GRIP #3139)
- `/onboard` — legacy onboarding entry (also redirects here)
- `tests/test_activate_grip_tui.py` — 37 Goodhart-proof tests
- Hypothesis H329 — falsifiable claim that the TUI++ flow ships with parity + delight
