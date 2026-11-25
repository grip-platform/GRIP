---
description: Queue steering items during active loops without breaking flow.
---

# /steer -- Session Steering Dispatcher

Fast, non-disruptive session steering. Queue fixes, enhancements, TODOs, or investigations without interrupting the current RSI/converge/broly loop.

## Usage

```bash
/steer fix <description>         # Queue fix_now item
/steer enhance <description>     # Queue fix_later enhancement
/steer todo <description>        # Append to MEMORY.md TODOs
/steer investigate <query>       # Spawn background Broly investigation
/steer status                    # Queue stats + wave position
/steer list                      # Show all pending items
/steer drain                     # Process fix_now items at wave pause
```

## Examples

```bash
/steer fix tests/test_queue.py assertion wrong on line 42
/steer enhance add retry logic to webhook sender
/steer todo review Toopie's PR before standup
/steer investigate why does converge timeout after depth 4
/steer status
```

## Behaviour

- Executes in < 200 tokens, returns control immediately
- Does NOT start new loops, switch branches, or create PRs
- `investigate` spawns a background Broly agent (only exception)
- All subcommands delegate to existing modules (zero new logic)

## Integration

Delegates to the `steering-sessions` skill which routes through `lib/session_steer.py` to:
- `lib/improvement_queue.py` (fix, enhance, status, list, drain)
- MEMORY.md TODOs section (todo)
- `/broly` skill (investigate)
