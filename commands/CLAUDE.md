# Commands Directory

Slash command definitions for GRIP.

## Contents

Command definitions in markdown format.

## Key Commands

### Session Management

| Command | Purpose |
|---------|---------|
| `/refresh-context` | Build mental model at session start |
| `/save` | Save all GRIP state for resumption |
| `/remind-yourself` | Search past conversations |
| `/preplan` | Prepare plan for next session |
| `/pair` | Monitor and steer other sessions |
| `/closeout` | Session-end ritual (commit, push, save, summary) |

### Development

| Command | Purpose |
|---------|---------|
| `/feature-complete` | Enhanced feature development |
| `/ui-complete` | Unified UI development |
| `/create-pr` | PR creation workflow |
| `/generate-e2e-tests` | E2E test generation |
| `/shipit` | Batch commit-push-PR workflow |
| `/sprint-loop` | Autonomous sprint with guardrails |

### Modes

| Command | Purpose |
|---------|---------|
| `/mode <name>` | Activate operating mode |
| `/mode off` | Deactivate current mode |
| `/mode status` | Show current mode |

### Infrastructure

| Command | Purpose |
|---------|---------|
| `/activate-grip` | Identity setup wizard |
| `/backup-grip` | Create infrastructure backup |
| `/auto-improve` | Run RSI cycle |
| `/learn` | Auto skill creation |
| `/detect-insights` | Run insights detection pipeline |
| `/grip-doctor` | GRIP health diagnostics |
| `/grip-inspect` | Infrastructure report (rules, gates, hooks, modes) |
| `/grip-config` | Settings wizard (statusline, rules, gates, modes) |

## Invoking Commands

Type the command in your message:
```
/create-pr
```

Claude will invoke the corresponding skill.
