---
name: dispatch
description: Coordinator+Converge dispatch procedure with the economic gate. Use before ANY Agent/Task fan-out, broly mesh, or AGORA council to decide depth, shape, and whether to dispatch at all. Thin procedural wrapper over rules/dispatch-economics.md (the gate) and rules/agent-dispatch-protocol.md (the structural contract).
status: active
triggers: ["/dispatch"]
integrates: [bg-dispatch, dfi-fib]
chains_to: bg-dispatch
---

# /dispatch — Coordinator + Converge with the economic gate

The InnerspeakerGo discipline applied to delegation: pick the work, reason
briefly about cost, execute at the lowest depth that meets the criterion,
zero ceremony. This skill is the **decision procedure**; the economics live
in `rules/dispatch-economics.md` and the per-agent contract in
`rules/agent-dispatch-protocol.md`. Do not restate them — apply them.

## When to invoke

Before any `Agent`/`Task` fan-out, recursive spawn, broly mesh, AGORA
council, or `/bg-dispatch`. Cheap to run on the main thread (ambient
already loaded — that is the whole point).

## The procedure (Coordinator role)

1. **State the deliverable + converge criterion.** One sentence each.
   "Done when X is true" — measurable, outcome-defined.

2. **Gate 1 — Capability.** Does the deliverable need a main-session-only
   tool (Task*, AskUserQuestion, ExitPlanMode, ScheduleWakeup)? If yes →
   STOP. Do it on the main thread. Never dispatch session-state mutation.

3. **Gate 2 — Economics.** Estimate useful-work-tokens-per-agent. If
   `< 3 × ~84k ambient floor` → do it at depth 0 (main thread or a
   deterministic script — no LLM). Only continue if it clears 3:1.

4. **Gate 4 — Pick the lowest depth** (dfi-fib ladder in the rule). Start
   at 0. You are only here if 0 failed. Justify each rung in one line.

5. **Gate 3 — Pick the shape.** Chain by default (floor paid once).
   Fan-out only if independent AND latency-bound AND each agent clears
   Gate 2 alone.

6. **Right-size the agent.** Leanest `subagent_type` whose tool set covers
   the work (`Explore` for read-only search, `direct-implementation` for
   edit/test/commit, `general-purpose` only when genuinely *). Fewer tool
   schemas = lower floor.

7. **Dispatch** per `rules/agent-dispatch-protocol.md`: one atomic
   deliverable per agent, anti-pause clause, `run_in_background=true`, an
   explicit `effort` (Claude Code 2.1.292+ — `low | medium | high | xhigh |
   max`; `/ultrado`'s FAST/CAREFUL/VERIFY -> low/high/xhigh
   mapping in `skills/ultrado/SKILL.md` is the reference for picking one;
   `CLAUDE_CODE_EFFORT_LEVEL`, which GRIP pins to `max`, overrides it until
   cleared),
   and a self-contained prompt (the agent has no session context — brief it
   like a colleague who just walked in). Then **yield** — do not poll;
   you are notified on completion.

## The Converge role (on completion)

8. **Criterion check.** Deliverable meets the stated criterion? → done,
   report tersely. Not met? → escalate exactly one depth rung with a
   documented reason, or hand back to the main thread. Never re-dispatch
   the identical prompt at the same depth (that is the polling-loop
   failure `rules/evo-dispatch-protocol.md` warns about).

## Anti-patterns (each one is a logged falsifier)

- Depth-3 fan-out without trying depth 0 first.
- Delegating to "free up the main thread" when the work is < 3:1 (the
  floor is re-paid; nothing was saved — latency hidden, cost doubled).
- `general-purpose` reflexively for narrow work.
- A council/mesh when one canonical artefact answers it (Rule 21).
- Treating `run_in_background` as cost reduction. It is latency hiding.

## Origin

2026-05-15 RCA: 5 agents × ~84k = ~420k tokens to discover an
undelegatable session-state mutation, fanned out at depth 3 with no gate.
This skill makes the gate a single invocable procedure.
