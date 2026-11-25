---
name: hypothesis-driven-development
description: Every PR registers a falsifiable prediction. Auto-verified at deadline. GRIP as empirical science.
effort: high
triggers:
  - PR creation during RSI loops
  - /converge completion
  - /shipit invocation
  - hypothesis, falsify, predict, verify
---

# Hypothesis-Driven Development

PARAMOUNT: Every improvement to GRIP is a scientific experiment.

## Core Protocol

### 1. Pre-Registration (at PR creation)

Before or immediately after creating a PR, register a hypothesis:

```bash
PYTHONPATH=$HOME/.claude python3 $HOME/.claude/lib/hypothesis_engine.py register \
  --pr PR_NUMBER \
  --claim "Human-readable prediction about this change's impact" \
  --metric METRIC_NAME \
  --prediction "increase|decrease|> N|< N" \
  --baseline CURRENT_VALUE \
  --deadline YYYY-MM-DD
```

Available metrics: `delight_composite`, `genome_fitness`, `hook_health`, `sentinel_coverage`, `test_count`

### 2. Deadline Verification (automated)

At each session start or /evo run, check for due hypotheses:

```bash
PYTHONPATH=$HOME/.claude python3 $HOME/.claude/lib/hypothesis_engine.py verify --all
```

Results: CONFIRMED / REFUTED / INCONCLUSIVE

### 3. Status Check

```bash
PYTHONPATH=$HOME/.claude python3 $HOME/.claude/lib/hypothesis_engine.py status
```

Reports: total hypotheses, confirmation rate, due items.

## Rules (PARAMOUNT)

1. **RSI loops MUST register at least one hypothesis per PR** — no exceptions.
   A PR without a hypothesis is an untested assumption.

2. **Hypothesis claims must be SPECIFIC and MEASURABLE** — not vague aspirations.
   Bad: "This will improve GRIP." Good: "DELIGHT composite will increase by > 0.05 within 14 days."

3. **Refuted hypotheses are VALUABLE** — they prove our prediction model is testable.
   A system that only confirms is not doing science. Target: 30-70% confirmation rate.
   Below 30% = poor predictions. Above 70% = suspiciously easy predictions (Goodhart risk).

4. **Never post-hoc rationalise a refutation** — if the prediction failed, say so.
   Update the hypothesis with notes explaining what was learned, not why it was "actually correct."

5. **Confirmation rate feeds into /evo genome fitness** — prediction accuracy is a selectable trait.

## Falsification of THIS Skill

This skill claims: "Pre-registering hypotheses improves GRIP's prediction accuracy over time."

Falsified by: Confirmation rate does not improve across 20+ hypothesis cycles (random walk, not learning).

Shadow metric: Are hypotheses getting easier (Goodhart) or genuinely more accurate (learning)?

## Integration Points

- `/shipit`: auto-prompts for hypothesis registration
- `/evo`: confirmation rate as fitness dimension
- `/falsify-self`: hypothesis engine is subject to its own protocol
- DELIGHT v2: hypothesis verification results affect composite via convergence_efficiency signal
