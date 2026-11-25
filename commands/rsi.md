# /rsi

Launch an RSI (Recursive Self-Improvement) session with the unified Fibonacci wave protocol.

## Usage

```bash
/rsi                    # Full unified wave protocol (Fibonacci + delegation + breathing) — DEFAULT
/rsi quick              # 30-min timebox, truncated wave (W1-W3 only)
/rsi --no-fibonacci     # Flat execution, no wave scheduling
/rsi --no-delegate      # Single-model, no Opus/Sonnet split
/rsi --depth N          # Override max convergence depth
```

## Protocol

1. Capture baseline via `python3 lib/auto_converge.py check`
2. Report test_pass_rate, reviews (the share of merged pull requests AGORA passed at the first sitting; `check --full` measures it, a quick check reads the cache) and composite_score; meta_work_pct is shown but not graded (GRIP#9668)
3. Launch `/converge broly auto` with RSI criterion
4. Execute unified wave protocol: batch size, delegation, breathing, and convergence depth move together
5. Track delight via `delight_snapshot()` at breathing pauses between waves

## Unified Wave Protocol

The default mode runs the full Fibonacci wave cycle (ascent W1-W8, descent W8-W1).
Delegation follows convergence depth — Opus solo for DETECTED patterns, progressive
Sonnet delegation as patterns reach CODIFIED/EMBODIED, then Opus takes back creative
control on descent for synthesis work.

See `skills/rsi-aliases/SKILL.md` for the full unified table.

## Implementation

Uses `skills/rsi-aliases/SKILL.md` and `lib/auto_converge.py`.
Cube W-axis mapping via `lib/cube.py` `ConvergenceDepth` enum.
