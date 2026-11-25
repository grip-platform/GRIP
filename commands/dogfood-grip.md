---
description: Dogfood GRIP across the stack -- probe that GRIP is present (~/.claude) and that its shared tree is canonical (on main and level), plus grip-tick freshness. Probe-only: GRIP installs itself, and the overnight loop prepares a brief for it, never an unattended PR.
disable-model-invocation: false
harness: universal
---

# /dogfood-grip

Load the stack-dogfood skill, scoped to GRIP:

```
Skill(skill="stack-dogfood", args="--product grip")
```

Direct CLI:

```
bin/grip-stack-dogfood status --product grip
bin/grip-stack-dogfood prove  --product grip
```

GRIP is probe-only here. Like HAL it holds the merge machinery and safety gates,
so the overnight loop only ever prepares a brief for it -- never an autonomous PR.
