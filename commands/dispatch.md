# /dispatch — Coordinator + Converge dispatch gate

Runs the economic dispatch decision procedure before any Agent/Task
fan-out, broly mesh, or AGORA council. Decides depth (dfi-fib ladder),
shape (chain vs fan-out), and whether to dispatch at all.

## Usage

```
/dispatch            # run the gate against the pending dispatch decision
```

## Implementation

```
Skill(skill="dispatch")
```

See `skills/dispatch/SKILL.md` for the procedure,
`rules/dispatch-economics.md` for the PARAMOUNT gate, and
`rules/agent-dispatch-protocol.md` for the per-agent contract.
