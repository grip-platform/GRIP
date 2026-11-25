---
name: context-extension
description: Adaptive Context Extension (ACE) - legacy model switching (current Opus defaults to 1M)
version: 1.1.0
effort: low
triggers:
  - /extend-context
  - context pressure
  - approaching context limit
  - need more context
---

# Context Extension (ACE) — Legacy

> **Legacy skill.** Current Opus generations default to 1M context, so ACE model
> switching is no longer needed for the 200K→1M upgrade. This skill is retained for
> state tracking and threshold management but the `/extend-context` command is
> effectively a no-op for Opus users.

## Architecture

```
Opus 1M (default)  — ACE state tracking still active for threshold management
```

## State File

`~/.claude/.model-state.json` tracks:
- `active_model`: Current model alias (opus, opus[1m])
- `context_window`: Current max (1000000 default on current Opus)
- `extended`: Boolean flag (legacy — Opus defaults to 1M)
- `switch_history`: Audit trail of switches

## Dynamic Thresholds

| Model | 70% Warning | 80% Critical | Soft Checkpoints |
|-------|-------------|--------------|------------------|
| Opus 1M (default) | 700K | 800K | 20%, 40%, 60% |
| Opus 1M (extended) | 700K | 800K | 10%, 20%, 30%, 40%, 50%, 60% |

## Legacy: When to Extend

With current Opus defaulting to 1M, extension is rarely needed. ACE state tracking
remains active for threshold management and statusline display.

## Switch Protocol (Auto-trigger)

When `/extend-context` is invoked:

1. **Confirm** via AskUserQuestion (required — never auto-switch without confirmation)
2. **Update state**: `python3 ~/.claude/lib/model_state_manager.py extend`
   - Writes `.model-state.json` with `extended: true`
   - Emits `[ACE:SWITCH] model=opus[1m] context=1000000`
3. **Execute switch**: Run `/model opus[1m]` immediately after step 2
   - Do NOT just print a reminder — execute the slash command now
4. **Confirm** to user: "Switched to Opus 1M. Thresholds updated."

For `/extend-context revert`:
1. Confirm via AskUserQuestion
2. Run `python3 ~/.claude/lib/model_state_manager.py revert`
3. Execute `/model opus` immediately
4. Confirm to user: "Reverted to Opus 1M (default)."

## Safety

- **Confirm before switching**: Always uses AskUserQuestion first (no exceptions)
- **RSI/autonomous mode**: At 70% warning, the hook advises compacting at next natural checkpoint.
  No auto-extension occurs; the agent should serialize and resume in a fresh session.
- **Session reset**: New sessions start on opus/1M (the current Opus default)
- **Env override**: `CONTEXT_GATE_MAX` via `lib/grip_config.py` always takes precedence
- **[ACE:SWITCH] signal**: Emitted by model-state-manager; hooks detect this for logging
- **1M unavailable**: every current Opus and Sonnet generation defaults to 1M; this
  bullet is retained only for older models that did not

## Integration Points

- `lib/grip_config.py`: `effective_context_max()` and `is_extended_context()` read state
- `context-gate-hook.py`: Reads `.model-state.json` for thresholds
- `statusline-command.sh`: Shows model segment (Opus 1M default)
- `broly/orchestrator.py`: Checks context requirements before spawning
- `instance-serializer.py`: Includes model_state in serialized instances
- `session-start.sh`: Resets to defaults (opus/1M since March 2026)
