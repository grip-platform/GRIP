# Quick Start Guide

Get productive with GRIP in 10 minutes.

## Installation

**1. Clone GRIP:**

```bash
git clone <private-repo-url> ~/.claude
```

**2. Run installer:**

```bash
cd ~/.claude && ./scripts/install.sh
```

**3. Reload shell:**

```bash
source ~/.zshrc  # or ~/.bashrc
```

## First Session

Start Claude Code in any project:

```bash
cd ~/your-project
claude
```

GRIP activates automatically. You'll see:

```text
[GRIP] Gen 1 | a5e3570c | 29 agents, 57 skills | code mode | main
```

On first run, GRIP prompts for identity setup via `/activate-grip`.

## Essential Commands

| Command | What It Does |
|---------|--------------|
| `/refresh-context` | Build mental model of codebase |
| `/create-pr` | Create PR with automated workflow |
| `/remind-yourself` | Search past session history |
| `/mode code` | Switch to code-focused mode |
| `/audit-efficiency` | Check for efficiency violations |
| `grip resume latest` | Continue last session |
| `grip list` | List available sessions |
| `grip fresh` | Fresh session with inherited context |
| `gg++` | Maximum context session (12k tokens) |

## Common Workflows

### Starting a New Feature

```text
User: /refresh-context
User: Add user authentication with OAuth
```

GRIP builds a mental model, then plans the implementation.

### Creating a Pull Request

```text
User: /create-pr
```

GRIP stages changes, writes commit message, pushes, and creates PR.

### Resuming Work

```bash
# In terminal
grip resume latest

# Or list sessions first
grip list
grip resume gen:5
```

### Searching Past Sessions

```text
User: /remind-yourself how did we implement the auth flow?
```

GRIP searches session history and returns relevant context.

### Switching Modes

```text
User: /mode strategy    # Strategic planning mode
User: /mode code        # Back to coding mode
User: /mode legal       # Contract drafting mode
```

## Key Concepts

### Safety Gates

GRIP blocks dangerous actions mechanically:

- Reading `node_modules` (50k+ token waste)
- Force pushing to main
- Committing secrets (.env files)

You can't persuade GRIP to bypass gates.

### Signatures (Team Mode)

If using GRIP with a team, signatures identify users:

```text
J>>: refactor the auth module
```

Configure in `~/.claude/facts/team.md`.

### Context Gate

At 80% context usage, GRIP halts and asks:

- Compact and continue?
- Start fresh session?
- Save checkpoint?

Never auto-compacts without asking.

## Next Steps

- **[Safety Gates](SAFETY-GATES.md)** - Understand mechanical enforcement
- **[Team Setup](TEAM_SETUP.md)** - Configure multi-user environment
- **[Troubleshooting](TROUBLESHOOTING.md)** - Common issues and fixes
- **[Launch Methods](LAUNCH-METHODS.md)** - All launch options (gg++, hot reload, flow mode)
- **[Glossary](GLOSSARY.md)** - GRIP terminology
