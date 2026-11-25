---
name: output-tier
description: Show or change GRIP's reply formatting (super-simple / medium / detailed).
---

# /output-tier

Invokes `bin/grip-output-tier` with the user's args. All logic in `lib/output_tier.py`.

User-facing tiers:
- `super-simple` → ELI5 register, broader audiences
- `medium` (DEFAULT) → concise + plain + load-bearing facts only
- `detailed` → audit-trail mode, RSI/traceability work

Internal back-compat names (still accepted): `eli5`, `simple`, `caveman_plain`.

Args:
- `(empty)` or `status` → prints current resolved tier
- `super-simple|medium|detailed` → sets tier
- `--resolve` → programmatic resolve (prints internal tier name)

Examples:
- `/output-tier medium` → set to medium
- `/output-tier` → show current
- `/output-tier status` → show current
- `/output-tier --resolve` → script-friendly internal name

Persists to `state/output-tier-config.json`. Resolution precedence:
`GRIP_OUTPUT_TIER` env > config file > default (`simple` / medium).
