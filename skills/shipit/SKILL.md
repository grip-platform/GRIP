---
name: shipit
description: "REDIRECT — use /auto instead. Shipit's ship-uncommitted workflow is now the default /auto shape."
status: Redirect
version: 2.0.0
redirect_to: auto
triggers:
  - /shipit
  - "ship it"
  - "commit and push"
  - "push and PR"
---

# shipit (Redirect)

This skill has been merged into the unified `/auto` entry point.

Use `skills/auto/SKILL.md` instead.

`/shipit` invocations are handled by Shape A (ship-uncommitted) of `/auto`.
That shape preserves every prior `/shipit` guarantee:

- Secrets scan before staging
- Goodhart pre-flight gate (`lib/goodhart_guard.py`)
- Tiered branch protection (feature: auto-allowed, protected: gate, force-push: blocked)
- Conventional commit message generation
- Optional PR creation on feature branches
- Anti-drift output after PR creation

For the full lifecycle (uncommitted → merged PR), invoke `/auto` directly.
