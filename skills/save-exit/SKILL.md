---
name: save-exit
description: Runs unified state persistence via the saving-state skill, then prints an actionable context-aware goodbye banner instructing the operator to run /compact or /exit manually. Use when wrapping up a mid-session work burst that does not warrant the full /closeout git+push+slack ceremony — for research pauses, operator handoffs, or pre-compact checkpoints.
status: Active
effort: low
priority: Standard
version: 0.1.0
invocable: true
invocable_name: save-exit
tokens: 400
triggers:
  - /save-exit
  - save and exit
  - mid-session wrap
  - wrap up session without closeout
  - save and say goodbye
integrates:
  - saving-state
  - grip_save.py
see_also:
  - saving-state
  - closeout
  - context-gate
---

# /save-exit — One-Command Mid-Session Goodbye

Lightweight mid-session wrap-up. Combines unified state persistence with a context-aware exit banner, without the git-commit + push + Slack ceremony of `/closeout`.

## Live Context (DCI)

**Git state:** !`cd ~/.claude && git status --short 2>/dev/null | head -5`
**Last save:** !`cd ~/.claude && tail -1 cache/sentinel.jsonl 2>/dev/null | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('ts','never'))" 2>/dev/null || echo "never"`
**RSI lock:** !`test -f ~/.claude/state/rsi/rsi-active.lock && echo "ACTIVE" || echo "inactive"`

## Usage

```bash
/save-exit                              # Auto-detect achievement from git log
/save-exit "finished the auth refactor" # Explicit achievement message
```

## What It Does

Three steps, in order:

1. **Persists all GRIP state** — Delegates to `lib/grip_save.py` via the `saving-state` skill. Flushes insights, syncs DNA genome, serialises instance JSON, writes session context, updates KONO embeddings. Same output and subsystems as `/save`.

2. **Reads current context gate status** — Calls `lib/gates/context_gate.py:get_context_utilisation_snapshot()`, which returns a structured dict (`pct`, `tokens_used`, `tokens_budget`, `session_id`) by reading the state cache that `context-gate-hook.py` writes on every tool call. The goodbye banner tailors its instruction based on how full the context is. Do NOT use `_get_context_gate_summary()` — that helper returns a memory+git deny-message string for the 85% gate trip, not a utilisation payload.

3. **Prints the goodbye banner** — A context-aware farewell that tells the operator exactly what to do next. At high context (≥70%) it recommends `/compact`; at moderate (40-70%) it offers both `/compact` and `/exit`; below 40% it just suggests `/exit` and notes that `/compact` is optional.

## Execution

The skill itself is a thin instruction layer. When `/save-exit` is invoked, Claude runs this sequence in the current turn:

```bash
# Step 1 — delegate to /save (achievement message optional)
grip-py ~/.claude/lib/grip_save.py "$ACHIEVEMENT"

# Step 2 — read structured context utilisation snapshot
python3 -c "from lib.gates.context_gate import get_context_utilisation_snapshot; import json; print(json.dumps(get_context_utilisation_snapshot()))"

# Step 3 — print the goodbye banner
# (rendered by Claude in the final response, not a separate tool call)
```

The banner is emitted as Claude's final turn text, not via a shell echo, because it's the message the operator reads before typing `/compact` or `/exit`.

### Goodbye Banner Template

```
─── Session persisted ───────────────────────────────────
  State:       {subsystems_saved}/{total_subsystems} saved
  Context:     {pct}% ({tokens_used}/{tokens_budget})
  Achievement: {achievement}

  → Recommended next step: {recommendation}
    {recommendation_reason}

  Goodbye.
─────────────────────────────────────────────────────────
```

`{recommendation}` is one of:
- `run /compact to free context and continue` (context ≥ 70%)
- `run /compact to free context, or /exit to close the session` (40% ≤ context < 70%)
- `run /exit to close the session, or keep working` (context < 40%)

## When to Use

- Before `/compact` when you want a clean persistence checkpoint first
- Handing off a session to another operator mid-task
- Stepping away for a meeting or break without committing WIP
- Pre-pause for an RSI breathing interval without a full ship cycle
- Any time `/save` feels too bare but `/closeout` feels too ceremonial

## What It Does NOT Do

Clarity matters — these are the boundaries with the adjacent skills:

| Not done by `/save-exit` | Handled by |
|--------------------------|------------|
| Commit / stage / push git changes | `/closeout` |
| Post Slack summary | `/closeout` |
| Stop caffeinate / deregister session / clean pair-mode sockets | `session-end.sh` |
| **Automatically run `/compact`** | The operator must type it — slash commands cannot be programmatically invoked from within a skill turn |
| **Automatically exit Claude Code** | Same as above — operator action required |
| Clear wave state or RSI lock | `session-end.sh` |
| Trigger a new hypothesis verification pass | `/save` (which `/save-exit` already delegates to) |

## Falsification

This skill is wrong if any of the following hold after three months of use:

- Operators report that `/save-exit` is indistinguishable from `/save` in practice, so the second command is just cognitive overhead
- The context-aware banner recommendation is wrong ≥50% of the time (operators ignore it)
- `/save-exit` never actually gets invoked because operators always jump to `/closeout` or `/compact` directly

If falsified, remove the skill and let `/save` stand alone with a richer output banner.

## Integration notes

- Reuses `lib/grip_save.py` as-is. Does NOT fork, wrap, or monkey-patch the save orchestrator.
- The context-gate summary helper is read-only and cheap (< 50 ms per call).
- Safe to invoke at any point in a session, including inside an active RSI loop — it does not touch the RSI lock.
- Compatible with the `saving-state` skill being invoked elsewhere in the same turn; `grip_save.py` is idempotent.
