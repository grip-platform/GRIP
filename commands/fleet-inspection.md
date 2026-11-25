---
description: Lands every running GRIP session's uncommitted work through PRs, then shows the operator what each session and GRIP as a whole did, on one short local page.
disable-model-invocation: false
harness: universal
---

# /fleet-inspection

Load and execute the fleet-inspection skill:

```
Skill(skill="fleet-inspection")
```

Three steps, one tool. `grip-fleet prompt --expect N` mints a run and copies the landing
prompt to the clipboard; the operator pastes it into every session. `grip-fleet wait`
reports each session's record as it arrives. Then read the records, write the short
`summary.json` the page leads with, and run `grip-fleet inspect --open`.

See `skills/fleet-inspection/SKILL.md` for the summary contract, the traps and the
falsification clause.
