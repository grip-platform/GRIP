---
description: Dogfood HAPPI across the stack -- check the happi runtime is on PATH (bash ~/.hal/HAPPI.md install), round-trip a real version envelope, confirm the HAL runtime source, and prove the install fixed point.
disable-model-invocation: false
harness: universal
---

# /dogfood-happi

Load the stack-dogfood skill, scoped to HAPPI:

```
Skill(skill="stack-dogfood", args="--product happi")
```

Direct CLI:

```
bin/grip-stack-dogfood status --product happi [--require-installed]
bin/grip-stack-dogfood prove  --product happi
```

HAPPI installs with one external step: `bash ~/.hal/HAPPI.md install` (puts
`happi` on PATH). It is probe-only in the framework, so `install` reports the
step rather than running it.
