# /gg — Good Game (session Victory Lap)

Render a delightful, deterministic scorecard of what GRIP shipped this session —
real git stats framed as a video-game victory screen. **Zero LLM, pure git** (so
it works even when providers are rate-limited).

## Usage

```bash
python3 ~/.claude/lib/victory_lap.py [--since "<git approxidate>"] [--label "<window>"]
```

- `--since` — window start, any git approxidate (e.g. `"8 hours ago"`, `"2026-06-02 00:00"`). Default `8 hours ago`.
- `--label` — friendly window label printed on the card.

## What it shows

- **Commits / Merges / Test commits** — counted from `git log --numstat` (test
  commits = those touching a file under a `tests/` directory, or whose name
  starts `test_` or ends `_test.py`).
- **Lines + / −** — total insertions / deletions in the window.
- **Streak** — consecutive UTC days with ≥1 commit, ending today (`on fire` at ≥3).
- **XP** — a transparent, gamified score: `commits*10 + merges*50 + test_commits*25 + added//10`.
- **Play of the session** — the commit with the largest net line change.

Every number is real and verifiable (Rule 17 / output-tier precision invariant —
**delight without lying**). The core (`parse_numstat_log`, `compute_stats`,
`compute_streak`, `render_card`) is pure and Goodhart-tested in
`tests/test_victory_lap.py` (11 tests).

## When to use

End of a sprint, an overnight RSI loop, or any session worth a victory lap.
Pairs with `/recap` (plain client recap), `/eod` (day wrap), and `/rave`
(milestone narration) — but `/gg` is the terse, gamified, zero-cost one.
