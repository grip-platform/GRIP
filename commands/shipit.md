---
description: Batch commit-push-PR workflow in a single command.
disable-model-invocation: false
---

# /shipit -- Batch Ship Workflow

Stages, commits, pushes, and optionally creates a PR in one flow.

## Usage

```bash
/shipit                      # Stage all, commit, push, PR
/shipit file1.py file2.py    # Stage specific files only
```

## Implementation

Load and execute the shipit skill:

```
Skill(skill="shipit")
```

## When to Use

- Feature work is ready to land
- Quick fixes that need immediate shipping
- When you want commit + push + PR without multiple steps
