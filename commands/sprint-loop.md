---
description: Autonomous sprint -- pick issues, implement, score, PR, merge.
disable-model-invocation: false
---

# /sprint-loop -- Autonomous Sprint

Self-healing RSI sprint that scans GitHub issues, implements fixes, scores locally via MoE, creates PRs, and gates via CI and Ralph.

## Usage

```bash
/sprint-loop                 # Full autonomous sprint
/sprint-loop --issues 3      # Limit to 3 issues
/sprint-loop --label "bug"   # Filter by label
```

## Implementation

Load and execute the sprint-loop skill:

```
Skill(skill="sprint-loop")
```

## Prerequisites

- `GRIP_AUTONOMOUS=1` or autonomous-state level >= 1
- GitHub CLI authenticated
- Issues labelled `sprint-loop` in the repository

## When to Use

- RSI sprint sessions
- Batch issue resolution
- Autonomous improvement loops
