---
name: grip-haiku
description: Turns the live GRIP genome fitness snapshot into a 3-line haiku + one-line health verdict. No LLM, no network — deterministic, beautiful, instant.
version: 1.0.0
status: Active
triggers:
  - "/grip-haiku"
  - "/haiku"
  - "how is grip feeling"
  - "genome haiku"
  - "grip health poem"
---

# /grip-haiku — GRIP Genome Haiku

One command. GRIP's inner state, rendered as poetry.

## Usage

```bash
python3 -m lib.grip_haiku            # coloured terminal output (default)
python3 -m lib.grip_haiku --no-colour  # plain text (for piping)
python3 -m lib.grip_haiku --json       # machine-readable JSON
python3 -m lib.grip_haiku --genome /path/to/genome.json  # custom genome
```

## What It Produces

```
──────────────────────────────────────────
  GRIP Genome Haiku  Gen 6336 · 25 genes · fitness 93.5%

  steady pulse tonight
  knowledge layers on knowledge
  the tide never stills

  strongest  design-principles (1.00)
  weakest   grip-first-thinking (0.80)

  GRIP is healthy — strong signal across most genes.
──────────────────────────────────────────
```

## Mood Tiers

| Fitness | Mood       | Colour |
|---------|------------|--------|
| ≥ 0.95  | thriving   | bright green |
| ≥ 0.85  | healthy    | green |
| ≥ 0.70  | stressed   | yellow |
| < 0.70  | struggling | red |

## Design

- Reads `data/genome-shared.json` (updated by `/evo` and genome pipeline)
- Haiku selection is deterministic: `gen_number % pool_size` — same gen, same poem
- No LLM call, no network, no dependencies beyond stdlib + the genome file
- Colour is auto-disabled when stdout is not a TTY

## Implementation

| File | Purpose |
|------|---------|
| `lib/grip_haiku.py` | Core library — `generate()`, `render()`, CLI |
| `tests/test_grip_haiku.py` | 28 Goodhart-resistant tests (28/28 pass) |

## When To Use

Run at session start for a quick pulse check on GRIP's genome health.
Pipe `--no-colour` output into `/morning` or a Slack message for a team delight moment.
