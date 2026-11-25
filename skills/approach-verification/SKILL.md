---
name: approach-verification
description: Pre-implementation gate that catches wrong-approach errors before code is written. Fires on multi-step tasks touching 3+ files. Use before starting a multi-file change, refactor, or migration.
---

# Approach Verification Gate

Pre-implementation verification that prevents wrong-approach errors in multi-step tasks.

## When to Trigger

This gate activates when ALL of these conditions are met:

1. Task involves **3+ file modifications** (multi-file change)
2. Task is **not trivially inferable** (single-line fix, typo, direct instruction)
3. **Multiple valid approaches** exist (architectural choice required)

### Trigger Signals

| Signal | Threshold | Example |
|--------|-----------|---------|
| Files to modify | >= 3 | "Refactor authentication" |
| Approach ambiguity | >= 2 valid options | "Add caching" (Redis vs memory vs file) |
| User rejection history | Last task rejected | Follow-up to "no, not like that" |
| Complexity estimate | Multi-step implementation | "Implement feature X with tests" |

### Skip Conditions (Do NOT trigger)

- User gave explicit, detailed instructions ("Add line X to file Y")
- Single-file change
- User said "just do it" or similar override
- Autonomous mode with no interactive channel

## Protocol

### Interactive Mode

When the gate triggers, use `AskUserQuestion` with 2-3 approach options:

```
question: "Which approach should I take for {task}?"
options:
  - label: "{Approach A} (Recommended)"
    description: "Pros: ... Cons: ..."
  - label: "{Approach B}"
    description: "Pros: ... Cons: ..."
  - label: "{Approach C}"
    description: "Pros: ... Cons: ..."
```

Rules:
- The recommended approach should be first
- Each option must include concrete trade-offs
- Include file list for each approach (which files will be modified)
- Keep descriptions to 1-2 sentences per option

### Autonomous Mode

When `GRIP_AUTONOMOUS=1` or no interactive channel:

1. Select highest-confidence approach
2. Log the choice and alternatives to KONO:
   ```
   python3 lib/kono_substrate.py store \
     --domain approach-verification \
     --key "task-{hash}" \
     --content "Selected: {approach}. Alternatives: {others}. Confidence: {pct}%"
   ```
3. Proceed with implementation
4. If approach fails (tests break, user rejects PR), fall back to second option

### Plan Mode Integration

When `EnterPlanMode` is used for multi-step tasks:

1. The plan MUST include an "Approach" section at the top
2. List alternatives considered with brief rationale for chosen approach
3. The user approves the plan (which includes the approach) via `ExitPlanMode`

This is the preferred path — plan mode already has the approval mechanism built in.

## Convergence Pattern

This gate is itself a tiny convergence loop:

```python
converge(
    state={"task": task_description, "approaches": []},
    criterion=lambda s: len(s["approaches"]) >= 2 and s.get("user_selected"),
    improve=lambda s: generate_next_approach(s),
    max_depth=3,
)
```

The gate converges when:
- At least 2 approaches are generated
- User has selected one (interactive) or highest-confidence is above threshold (autonomous)

## Metrics

Track approach verification effectiveness via:

```json
{
  "event": "approach_verification",
  "triggered": true,
  "mode": "interactive|autonomous",
  "approaches_presented": 3,
  "user_selected": 1,
  "task_hash": "abc123",
  "outcome": "success|rejection|fallback"
}
```

Log to `data/gate-metrics.jsonl` for inclusion in insights pipeline.

## Evidence

From GRIP Insights MESH Analysis (277 sessions):
- 83 wrong_approach events (4.3% error rate) — largest friction category
- 174 user rejections — user frequently says "no" to agent suggestions
- Multi-file changes (73 instances) correlate with wrong-approach risk
- Estimated 64% reduction in wrong-approach with verification gate

## Related

- `@rules/efficiency-rules.md` Rule 7 — AskUserQuestion MANDATORY
- `@rules/efficiency-rules.md` Rule 2 — Plan Evaluation Gate
- `EnterPlanMode` — Claude Code native plan mode tool
- `lib/converge.py` — Convergence kernel for approach generation
