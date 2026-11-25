---
name: ascent
description: The Ascent of GRIP — render the genome as a March-of-Progress where each figure's posture is a gene's REAL measured fitness, marched weakest → upright-with-a-MacBook. A delight surface that refuses to flatter. Use when V>> invokes /ascent, wants to see GRIP's evolution, or wants an honest one-glance read of which genes are strong vs hunched. Sibling to /grip-haiku (same genome, different art).
---

# The Ascent of GRIP

Turns the live genome into the iconic knuckle-ape → upright-human-with-laptop
silhouette. Each figure is a **real gene** and its **posture is that gene's real
fitness** — a low-fitness gene stays a hunched ape, a high-fitness gene stands up
and carries the MacBook. Sorted weakest → strongest, the genome marches across the
page on a ground line, crowned with a live self-monitor dashboard and a full-name
legend (fitness bars + bands) — nothing ellipsized.

## Why it is delight bound to truth

The posture is a deterministic projection of fitness, so it **cannot flatter**:

- Flat genome → one flat row of identical postures (honest "no measured ascent").
- Weak gene → hunched ape, fitness printed beside it (verifiable against the file).

This is the shadow-metric / falsify-self principle made visual — the opposite of a
vanity counter going up. Pure stdlib, deterministic (same genome → same march).

## Run

```bash
~/.claude/bin/grip-ascent              # richest genome, colour if a TTY
~/.claude/bin/grip-ascent --no-colour  # plain text
~/.claude/bin/grip-ascent --colour     # force colour (pipe to `less -R`)
~/.claude/bin/grip-ascent --figures 12 # longer march (12 figures)
~/.claude/bin/grip-ascent --open       # open in your text editor (best for wide marches)
~/.claude/bin/grip-ascent --html       # colour HTML version, opens in your browser
~/.claude/bin/grip-ascent --json       # machine-readable
```

**Wide marches mangle in a terminal** (ANSI escapes + line-wrap). Two clean exits:

- `--open` writes the plain-text art to a temp `.txt` and opens it in the platform's
  default text editor — macOS `open -t` (TextEdit), Windows `notepad`, Linux
  `xdg-open` — where the box-drawing, figures, and bars line up cleanly.
- `--html` writes a self-contained colour HTML document and opens it in your
  browser — colour **and** clean alignment, the best of both.

Colour resolves as: `--colour`/`--no-colour` win, then `NO_COLOR` / `FORCE_COLOR`
(the no-color.org conventions), else stdout-is-a-TTY auto-detect.

Or directly: `python3 -m lib.evolution_march [--colour|--no-colour] [--open] [--html] [--json] [--genome PATH] [--figures K]`

## How it works

- `lib/evolution_march.py` — pure renderer. `band_for(fitness)` is the monotone
  posture map; `build()` sorts ascending; `render()` composes the ASCII march.
- Default genome = the richest available (most distinct fitness values), so the
  march has real spread; falls back to `data/genome-shared.json`.
- A live **self-monitor** header (Gen · git branch · dirty-tree count · genome
  health) is read from git + the genome — the art is GRIP watching itself.
- Figures carry short index labels (1..N); a **full-name legend** beneath lists
  every gene's complete name, fitness, and a fitness bar — nothing is ellipsized.
- Every number on screen is verifiable against the genome file.

## Guarantees (Goodhart-safe, `tests/test_evolution_march.py`)

- Posture is monotone non-decreasing in fitness (a mutation that makes apes stand
  up regardless of fitness fails the suite).
- The laptop appears **iff** a gene is thriving (≥0.95).
- Uniform fitness renders uniform posture — no fabricated spread.
- Deterministic render (SHA-stable for a fixed genome).

Sibling: `/grip-haiku` (same genome, haiku form). Distinct from `genome_animator`
(generation-over-generation frames; this is per-gene posture).
