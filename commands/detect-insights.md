---
description: Run insights detection pipeline -- patterns, skills, red-team, issues.
disable-model-invocation: false
---

# /detect-insights -- Insights Pipeline

Detect patterns in GRIP usage data, propose skills, run red-team analysis, and create GitHub issues for improvements.

## Usage

```bash
/detect-insights                    # Full 7-stage pipeline
/detect-insights --detect           # Pattern detection only
/detect-insights --red-team         # Red-team analysis only
```

## Implementation

Load and execute the insights-pipeline skill:

```
Skill(skill="insights-pipeline")
```

## When to Use

- After completing an RSI sprint phase
- After 5+ PRs merged (weekly trend detection)
- Periodic quality checks
- When running autonomous improvement loops
