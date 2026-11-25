---
name: steering-sessions
description: Non-disruptive session steering dispatcher for queuing fixes, enhancements, TODOs, and investigations during active loops.
status: Active
effort: low
version: 1.0.0
created: 2026-03-09
generation: 470
triggers:
  - /steer
  - "queue this fix"
  - "add enhancement"
  - "fix this bug later"
  - "add to TODO"
  - "investigate with broly"
  - "steer status"
integrates:
  - broly-meta-agent
  - focus-cards
priority: Medium
---

# Steering Sessions

Non-disruptive session steering during active RSI, converge, or broly loops. Queues micro-commands without breaking flow.

## Core Protocol

Parse the `/steer` subcommand and delegate to `lib/session_steer.py`:

| Subcommand | Route | Module |
|------------|-------|--------|
| `fix <desc>` | `steer_fix(desc)` | ImprovementQueue (fix_now) |
| `enhance <desc>` | `steer_enhance(desc)` | ImprovementQueue (fix_later) |
| `todo <desc>` | `steer_todo(desc)` | MEMORY.md TODOs section |
| `investigate <query>` | `steer_investigate(query)` | Broly meta-agent (background) |
| `status` | `steer_status()` | Queue stats + wave position |
| `list` | `steer_list()` | All pending items |
| `drain` | `steer_drain()` | Process fix_now items |

## Execution Contract

- Token budget: < 200 per invocation
- Must NOT start new converge/RSI loops
- Must NOT switch branches or create PRs
- Must NOT break the current wave rhythm
- Only `investigate` spawns a background agent

## Implementation

When `/steer` is invoked, run the appropriate function from `lib/session_steer.py`:

```bash
# CLI usage (for bash hooks)
python3 ~/.claude/lib/session_steer.py fix "description here"
python3 ~/.claude/lib/session_steer.py status
```

```python
# Python usage (for skill invocation)
import sys
sys.path.insert(0, str(Path.home() / ".claude" / "lib"))
from session_steer import steer_fix, steer_status
```

For `investigate`, after getting the Broly prompt from `steer_investigate()`, invoke `/broly` as a background Agent with the returned prompt.

## YSH Origin

Pattern observed 3x+ across sessions: queued micro-commands during active RSI/converge loops were handled as ad-hoc natural language, interrupting flow. This skill unifies the entry point (YSH: proven pattern, abstract now).
