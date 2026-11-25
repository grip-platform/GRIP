---
description: Spawn and manage worker agents for delegated task execution
disable-model-invocation: false
---

# Delegate

Spawn worker agents (via Teams API) for parallel task execution. Extracted from
pair-mode v2.0 local worker spawning -- now a standalone command.

## What It Does

Launches background Claude Code worker agents (Sonnet) to execute tasks
autonomously while the main session continues planning or reviewing.

## Usage

```bash
# Spawn a worker for a task
/delegate "Refactor the auth middleware to use JWT validation"

# Check worker status
/delegate status

# Send steering guidance to active worker
/delegate steer "Use the existing token cache, don't create a new one"

# Graceful shutdown of active workers
/delegate stop

# Route to team delegation skill (multi-member)
/delegate team
```

## Implementation

### `/delegate "task"` (Spawn Worker)

1. Validate task description is provided
2. Spawn worker agent via `lib/rsi_delegate_launcher.py`:
   - Launches `claude --dangerously-skip-permissions` (Sonnet model)
   - Captures session ID from process output
   - Writes state to `.rsi-delegate-state.json`
3. Connect to worker via `grip_pair_connect` for monitoring
4. Display worker session ID and monitoring status

### `/delegate status`

1. Read `.rsi-delegate-state.json` for active workers
2. Display: worker count, session IDs, task descriptions, runtime
3. Show Task API status via TaskList if available

### `/delegate steer "text"`

1. Send steering command to active worker session
2. Uses `grip_pair_steer` with `inject_context` type
3. Worker receives guidance without interrupting current operation

### `/delegate stop`

1. Send graceful shutdown signal to all active workers
2. Workers finish current operation, save progress, then exit
3. Clean up state files and session connections

### `/delegate team`

1. Route to `delegating-team-work` skill
2. Matches tasks to team member capabilities
3. Creates GitHub issues and Slack notifications

## Related

- `~/.claude/lib/rsi_delegate_launcher.py` -- Worker lifecycle management
- `~/.claude/agents/pair-worker.md` -- Worker agent definition
- `~/.claude/skills/delegating-team-work/SKILL.md` -- Team delegation skill
- `~/.claude/lib/session_control_server.py` -- WebSocket monitoring
- `/pair` -- Real developer-to-developer pair programming (v3.0)
