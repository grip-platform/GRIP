---
description: Dogfood the CodeTonight AI suite (GRASP, HAPPI, HAL, GRIP) onto itself -- census, idempotent-fold install proof, grip-tick reporting, and a gated overnight loop that opens draft PRs only on the public products and never merges.
disable-model-invocation: false
harness: universal
---

# /stack-dogfood

Load and execute the stack-dogfood skill:

```
Skill(skill="stack-dogfood")
```

Direct CLI:

```
bin/grip-stack-dogfood status [--product X] [--require-installed] [--json]
bin/grip-stack-dogfood report [--product X]
bin/grip-stack-dogfood install --product X [--harness H] [--dry-run]
bin/grip-stack-dogfood prove [--product X]
```
