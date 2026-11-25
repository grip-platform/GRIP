---
description: Dogfood GRASP across every harness -- probe where the grasp-mcp server is registered, install it where missing (codex, gemini, opencode, hermes, amp, grok, qwen, Claude Code, Cursor, Claude desktop), and prove the install fixed point.
disable-model-invocation: false
harness: universal
---

# /dogfood-grasp

Load the stack-dogfood skill, scoped to GRASP:

```
Skill(skill="stack-dogfood", args="--product grasp")
```

Direct CLI:

```
bin/grip-stack-dogfood status  --product grasp
bin/grip-stack-dogfood install --product grasp [--harness H] [--dry-run]
bin/grip-stack-dogfood prove   --product grasp
```
