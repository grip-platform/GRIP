---
name: scientific-measurement
description: DELIGHT v2 + conversation entropy + shadow metrics measurement protocol. Popper + Shannon applied to session quality.
effort: medium
triggers:
  - breathing pause between Fibonacci waves
  - session end / /closeout
  - /evo invocation
  - delight, entropy, measurement, quality, session score
---

# Scientific Measurement Protocol

PARAMOUNT: Session quality is measured, not assumed. Every metric has a shadow.

## Measurement Points

### 1. DELIGHT v2 Snapshot (at breathing pauses)

```bash
PYTHONPATH=$HOME/.claude python3 $HOME/.claude/lib/delight_v2.py snapshot --session-hours N
```

Captures:
- **User delight** (6 signals): flow, velocity, completion, friction-to-flow, recovery time, momentum
- **System health** (3 signals): genome trend, converge velocity, convergence efficiency
- **Shadow metrics**: velocity_shadow (revert rate), flow_shadow (confusion proxy)
- **95% CI** via bootstrap resampling (200 samples)
- **Goodhart protection**: shadow metrics DISCOUNT gaming behaviour

### 2. Conversation Entropy (at session end)

```bash
PYTHONPATH=$HOME/.claude python3 $HOME/.claude/lib/conversation_entropy.py --window 120
```

Captures:
- **Tool entropy**: Shannon entropy of tool usage distribution
- **Event entropy**: Shannon entropy of hook event distribution
- **Temporal entropy**: how evenly distributed actions are over time
- **Inverted-U score**: peak at 0.70 normalised entropy (falsifiable hypothesis)

### 3. Trend Analysis (at /evo)

```bash
PYTHONPATH=$HOME/.claude python3 $HOME/.claude/lib/delight_v2.py trend --days 30
```

Reports Cohen's d effect size:
- |d| < 0.2: negligible (noise)
- 0.2-0.5: small (real but modest)
- 0.5-0.8: medium (meaningful)
- > 0.8: large (significant)

## Rules (PARAMOUNT)

1. **Never celebrate velocity without checking velocity_shadow** — high speed + high revert rate = gaming
2. **Never report DELIGHT without CI** — a point estimate without confidence interval is misleading
3. **Entropy is a HYPOTHESIS, not a fact** — the inverted-U peak at 0.70 is a testable prediction
4. **Cohen's d replaces "improving/declining"** — report effect SIZE, not just direction
5. **Shadow metrics can only DECREASE scores** — they are antibodies, not boosters

## Falsification of THIS Skill

Claim: "Measuring with shadow metrics produces more accurate session quality assessment than v1."

Falsified by: v2 composite does not correlate better with user-reported satisfaction than v1 composite.

Shadow metric: Are we measuring MORE but learning LESS? (measurement overhead vs signal quality)

## Integration

- `/rsi`: DELIGHT snapshot at each breathing pause
- `/evo`: trend analysis feeds genome fitness
- `/closeout`: entropy measurement before session end
- `/sprint-status`: DCI includes DELIGHT + entropy live data
- Hypothesis engine: every measurement claim is pre-registered
