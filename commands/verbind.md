# /verbind — coin a slash verb, bind it through one Koppel round

Type `/verbind <new-verb> <what you want>`. GRIP asks you once what the verb
should mean, binds it to one existing read-only operation, and applies it. The
next `/verbind <new-verb> ...` runs it with no question. A verb you defer is
never asked about again.

## Run

```bash
grip-verbind gate --prompt "/verbind shipwreck the stale branches"
grip-verbind refine shipwreck --kind skill --target check-reachable --note "list what exists"
grip-verbind refine shipwreck --defer
grip-verbind list [--readonly]
grip-verbind forget shipwreck
grip-verbind ultralearned shipwreck --seal 530ddaa79e8086d4
```

All output is JSON. Only the operator's own interactive session can bind a
verb or apply a binding; relayed and headless prompts never do, because
authority is checked again on every use. Every decision leaves a chained
receipt naming the outcome it actually took in `state/verbind/`, and a
hash-only leaf on the IDR chain.

Full skill: `skills/verbind/SKILL.md`. Library: `lib/verbind/`. Adapter hook:
`hooks/verbind-hook.py` (not bound until you activate it). Contract:
`docs/VERBIND.md`.
