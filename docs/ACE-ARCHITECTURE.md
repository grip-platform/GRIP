# ACE Architecture (Legacy)

Adaptive Context Extension - intelligent model switching between Opus 1M (default since March 2026), Opus Fast 1M, and Sonnet 1M.

## Overview

GRIP defaults to Opus with a 1M context window. ACE's original purpose (extending from 200K to 1M) is now legacy since Opus 4.6 defaults to 1M. ACE remains useful for switching to Sonnet 1M for cost optimisation. All context gate thresholds adjust dynamically.

```
Session Start
     |
     v
Opus 1M (default)
     |
     |-- User runs /extend-context (for Sonnet cost savings)
     |
     v
Sonnet 1M (extended)
     |
     |-- /extend-context revert
     |-- Session ends
     |
     v
Opus 1M (reset)
```

## Components

| Component | File | Role |
|-----------|------|------|
| Model State Manager | `lib/model_state_manager.py` | State file CRUD, extend/revert/reset |
| Context Gate Hook | `hooks/context-gate-hook.py` | Dynamic thresholds from model state |
| Statusline | `statusline-command.sh` | Display active model segment |
| Instance Serializer | `lib/instance_serializer.py` | Capture model_state on serialize |
| Session Start | `hooks/session-start.sh` | Reset to opus/200K on new session |
| Broly Orchestrator | `lib/broly/orchestrator.py` | Context requirement checks |
| Broly Power Levels | `lib/broly/power_levels.py` | Per-level context requirements |

## State File

Location: `~/.claude/.model-state.json`

```json
{
  "active_model": "opus",
  "context_window": 200000,
  "extended": false,
  "switch_history": [
    {
      "from": "opus",
      "to": "sonnet[1m]",
      "action": "extend",
      "timestamp": "2026-02-07T10:00:00+00:00",
      "session_id": "abc123"
    }
  ]
}
```

Fields:

| Field | Type | Description |
|-------|------|-------------|
| `active_model` | string | Current model alias: `opus`, `opus-fast`, `sonnet`, `opus[1m]` |
| `context_window` | integer | Max tokens: 200000 or 1000000 |
| `extended` | boolean | Whether ACE extension is active |
| `switch_history` | array | Last 20 switch events (audit trail) |

## Dynamic Thresholds

Context gate hook reads `context_window` from state file and computes thresholds as percentages.

### Opus 1M (default)

| Threshold | Percentage | Tokens | Action |
|-----------|-----------|--------|--------|
| Soft checkpoints | 20%, 40%, 60% | 200K, 400K, 600K | Background snapshot |
| Warning | 70% | 700K | systemMessage warning |
| Critical | 80% | 800K | BLOCK all tools |

### Opus Fast 1M (extended via /fast)

Opus 4.6 fast mode (CC 2.1.50+) uses the same Opus model with faster output and full 1M context. Toggled via `/fast` in Claude Code. ACE thresholds match Sonnet 1M.

| Threshold | Percentage | Tokens | Action |
|-----------|-----------|--------|--------|
| Soft checkpoints | 20%, 40%, 60% | 200K, 400K, 600K | Background snapshot |
| Warning | 70% | 700K | systemMessage warning |
| Critical | 80% | 800K | BLOCK all tools |

### Sonnet 1M (extended)

| Threshold | Percentage | Tokens | Action |
|-----------|-----------|--------|--------|
| Soft checkpoints | 20%, 40%, 60% | 200K, 400K, 600K | Background snapshot |
| Warning | 70% | 700K | systemMessage warning |
| Critical | 80% | 800K | BLOCK all tools |

### Environment Override

`CONTEXT_GATE_MAX` environment variable always takes precedence over model state.

## Broly Integration

Power levels define context requirements:

| Level | Recommended Context | Extended Required | Team Pattern |
|-------|-------------------|-------------------|--------------|
| THREAD | 1M | No | None |
| FORK | 1M | No | None |
| CLUSTER | 1M | No | Swarm |
| PIPELINE | 1M | Yes | Pipeline |
| MESH | 1M | Yes | Council |

PIPELINE and MESH levels require 1M context, which is now the Opus default. The orchestrator checks `requires_extended_context()` before spawning but this check passes automatically with the default Opus 1M window.

## Commands

### `/extend-context` (Legacy)

Switch to Sonnet 1M (for cost optimisation):
1. Runs `python3 ~/.claude/lib/model_state_manager.py extend`
2. Issues `/model sonnet[1m]`
3. Thresholds remain at 1M scale

### `/extend-context revert`

Switch back to Opus 1M:
1. Runs `python3 ~/.claude/lib/model_state_manager.py revert`
2. Issues `/model opus`
3. Thresholds remain at 1M scale

### `/extend-context status`

Display current model state, context window, and switch history.

## Session Lifecycle

1. **Session start**: `session-start.sh` runs `model-state-manager.py reset`, returning to opus/1M defaults
2. **During session**: User or system triggers `/extend-context` as needed
3. **Serialization**: `instance-serializer.py` captures `model_state` in the serialized instance
4. **Session end**: State persists in file but next session resets

## Edge Cases

| Scenario | Behavior |
|----------|----------|
| Revert from Sonnet to Opus | Both use 1M, no threshold change |
| `CONTEXT_GATE_MAX` env var set | Env var overrides model state file |
| State file missing/corrupt | Falls back to opus/1M defaults |
| Multiple rapid switches | History capped at 20 entries |

## Cost Awareness

| Model | Input Cost | Output Cost | Max Context |
|-------|-----------|-------------|-------------|
| Opus | ~$15/MTok | ~$75/MTok | 1M |
| Sonnet 1M | ~$3/MTok | ~$15/MTok | 1M |

Sonnet is cheaper per token, but the larger window processes more total tokens per session.
