# Scientific Method (PARAMOUNT)

Origin: RSI Sprint 2026-03-23. Novel contributions applying Popper + Shannon to AI tooling.

## Hypothesis-Driven Development

Every PR in an RSI loop MUST register a falsifiable hypothesis via the hypothesis engine.
No exceptions. A PR without a hypothesis is an untested assumption.

```bash
PYTHONPATH=$HOME/.claude python3 $HOME/.claude/lib/hypothesis_engine.py register \
  --pr NUMBER --claim "CLAIM" --metric METRIC --prediction PREDICTION --deadline DATE
```

At session start: `verify --all` to check due hypotheses.
At /evo: confirmation rate feeds genome fitness.

Target confirmation rate: 30-70%. Below 30% = poor predictions. Above 70% = suspiciously easy.

## Measurement Protocol

At breathing pauses between Fibonacci waves:
```bash
PYTHONPATH=$HOME/.claude python3 $HOME/.claude/lib/delight_v2.py snapshot --session-hours N
```

At session end:
```bash
PYTHONPATH=$HOME/.claude python3 $HOME/.claude/lib/conversation_entropy.py --window 120
```

## Shadow Metrics (Goodhart Protection)

Never celebrate a metric without checking its shadow:
- velocity -> velocity_shadow (revert rate)
- flow_state -> flow_shadow (confusion proxy)
- entropy -> inverted-U (extremes are bad)

Shadow metrics can only DECREASE scores. They are antibodies, not boosters.

## Effect Size (Cohen's d)

Report effect SIZE, not just direction. "Improving" means nothing without magnitude:
- |d| < 0.2: negligible (noise, don't act on it)
- 0.2-0.5: small (real but modest)
- 0.5-0.8: medium (worth attention)
- > 0.8: large (significant change)

## Falsification Awareness

Every signal, metric, and claim must state what would disprove it.
The hypothesis engine's falsification registry is the canonical reference.
A system that only confirms is not doing science.
