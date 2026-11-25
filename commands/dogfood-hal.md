---
description: Dogfood HAL across the stack -- probe the gateway on port 4010 and the grip-harness wiring. Probe-only: HAL wiring needs a dry run shown to the operator before it is applied, and the overnight loop prepares a brief for HAL, never an unattended PR.
disable-model-invocation: false
harness: universal
---

# /dogfood-hal

Load the stack-dogfood skill, scoped to HAL:

```
Skill(skill="stack-dogfood", args="--product hal")
```

Direct CLI:

```
bin/grip-stack-dogfood status --product hal
bin/grip-stack-dogfood prove  --product hal
```

HAL is probe-only here. It holds the merge machinery and the provider wiring, so
the overnight loop only ever prepares a brief for it -- never an autonomous PR.
