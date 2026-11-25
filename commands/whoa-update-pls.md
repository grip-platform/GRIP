---
description: Hold-the-fuck-up status snapshot. Reads every state surface, sorts into Live / Open / Backlog, runs a HITL 1-2-3 wizard. V>>'s voice, no AI fluff.
disable-model-invocation: false
---

# /whoa-update-pls — Status Snapshot

Run this when you've context-switched and want to know where everything
is in one pass. Output is plain English, three buckets, decisions
extracted via 1-2-3 wizard.

## Usage

```bash
/whoa-update-pls            # Full snapshot + wizard
/whoa-update-pls --no-ask   # Snapshot only, skip the wizard
/whoa-update-pls --delegate # Focus on items that can be handed off to the team
```

## Implementation

Load and execute the whoa-update-pls skill:

```
Skill(skill="whoa-update-pls")
```

## What it does

Three buckets in one pass:

1. **Live** — what's running right now (agents, yolo, RSI/ultrado loops,
   PRs in CI, concurrent sessions)
2. **Open, needs closure** — this session's debt (PRs awaiting merge,
   hypotheses awaiting verification, branches awaiting cleanup)
3. **Backlog** — pending tasks, open sprint-loop issues, deferred items

Each item gets one-line **why** (future advantage to GRIP/HAL),
**what** (the concrete work), **how** (the mechanism / next action).
Then a HITL wizard offers 1-2-3 (close off / hand off / defer) per
item that needs your call.

## Aliases

`/ultrawhao` resolves to the same skill. Both work.

## Related

- `/hitl` — unified HITL entry point
- `/asking-users` — AskUserQuestion protocol
- `/creating-wizards` — multi-step wizard pattern
- `/delegate` — hand items to CodeTonight team
- `/sprint` — escalate to a multi-item delivery if 3+ items need code edits
