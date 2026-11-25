---
name: ship-card
description: Render a deterministic terminal celebration at the moment work ships — a PR merge, a sprint close, a wave landing. Use when invoking /ship-card or marking a shipping moment.
---

# /ship-card — GRIP Ship Card (shipping-moment delight)

Render a deterministic, left-ruled terminal celebration at the moment work ships
— a PR merge, a sprint close, a wave landing. The dopamine hit of shipping, made
visible. Backed by the pure-stdlib lib `lib/ship_card.py` (Goodhart-testable,
no clock, no randomness).

## When to use

- A PR just merged → celebrate what shipped.
- A sprint / RSI wave completed → momentum recap.
- /auto-ship Stage 6 (post-merge), /closeout, or any "we shipped" moment.
- Operator types `/ship-card`.

## Protocol

1. **Gather** (best-effort, never block the ship on it):
   - **Title** — the PR title / sprint name / one-line "what shipped".
   - **Wins** — 2–4 plain-language lines of what landed. No jargon (Rule 17);
     a non-GRIP reader should understand each line.
   - **Momentum** — a short series of per-step counts (e.g. commits per wave, or
     `[files, tests, commits]`) for the unicode sparkline.
   - **Stats** — short fragments: `"3 commits"`, `"97 tests"`, `"4 files"`.

2. **Render** via the pure lib (no jargon leaks — the lib only formats):

   ```bash
   PYTHONPATH=$HOME/.claude $HOME/.claude/venv/bin/python -c "
   from lib.ship_card import render_ship_card
   print(render_ship_card(
       '<title>',
       ['<win 1>', '<win 2>'],
       [<momentum ints>],
       ['<stat 1>', '<stat 2>'],
   ))"
   ```

3. **Show** the card to the operator. On /closeout it MAY be sent to the
   operator's WhatsApp per the auto-broadcast policy — the card is plain text and
   renders cleanly inside a WhatsApp triple-backtick monospace block.

## Quick demo

```bash
PYTHONPATH=$HOME/.claude $HOME/.claude/venv/bin/python $HOME/.claude/lib/ship_card.py \
  "feat: my thing" "shipped the parser" "added 12 tests"
```

## Design (why it is correct-by-construction)

- **Pure + deterministic** — same ship ⇒ byte-identical card. The closing
  flourish is chosen by `SHA-256(title) % len(flourishes)`, never random or
  time-based, so the card is fully testable and reproducible.
- **Left-ruled, not right-bordered** — framed by a left rule (`┃`) with no right
  border, so it needs no display-width padding. The diamond `◆` and the spark
  ticks `▁▂▃▄▅▆▇█` are East-Asian-Width *ambiguous* (width-2 on some terminals);
  a padded right border would misalign there, a shared left prefix never does.
- **Zero non-stdlib deps** — `hashlib` + `dataclasses` only. Mirrors the proven
  pure-lib delight pattern of `lib/rave_card.py` and `lib/handoff_chime.py`.

## Tests

`tests/test_ship_card.py` — 16 Goodhart-resistant tests: exact sparkline ramps,
determinism (same title ⇒ same render), the left-rule alignment invariant (RED
if a padded right-border design is reintroduced — the bug this design prevents),
flourish-from-curated-set, blank-line hygiene, and empty-input robustness.
