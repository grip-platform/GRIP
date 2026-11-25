# /aegis — the safety-shield delight card

Render GRIP's standing roster of guardians — the deterministic gates that DENY a
dangerous tool call before it runs. Reframes "the gate blocked me" (friction) as
"the gate saved me" (guardian). Every block is a disaster that did not happen.

## Run

```bash
python3 -m lib.aegis_card                   # the standing shield
python3 -m lib.aegis_card --live            # + real recent catches from logs/
python3 -m lib.aegis_card --occasion "dawn" # the occasion seeds the motto
```

The roster is real (sourced from `docs/SAFETY-GATES.md` + the PreToolUse hooks),
grouped CRITICAL / HIGH / GUARD by blast radius. `--live` is best-effort and never
fails on a missing log.

Full skill: `skills/aegis/SKILL.md`. Backend: `lib/aegis_card.py` (pure,
deterministic, 16 test cases). Sibling delight: `/verdict` (hypothesis
resolution), `/aurora` (night's work), `/ship-card` (ship), `/gg` (victory).
