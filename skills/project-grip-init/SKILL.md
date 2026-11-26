---
name: project-grip-init
description: >
  Initialises GRIP infrastructure for new projects with session inheritance and identity checks.
  Use when first starting GRIP in an uninitialised project or after cloning to new machine.
status: Active
effort: medium
version: 1.0.0
triggers:
  - /init-grip
  - First session in project without .claude/CLAUDE.md
token_budget: 500
---

# Project GRIP Initialisation

Initialises GRIP infrastructure for a new project, ensuring session inheritance and identity checks work correctly on any machine.

## Problem

When cloning a project to a new machine or starting GRIP in an uninitialised project:
- `grip resume latest` fails (no session history)
- No instance JSON with next_up field exists
- Identity check doesn't trigger (no project CLAUDE.md)

## Solution

Create the following structure:

### In Project Directory

```
.claude/
├── CLAUDE.md           # Project-specific rules + identity check trigger
└── (session state stored in instance JSON next_up field)
```

### In ~/.claude/projects/{encoded-path}/

```
grip/
├── active-sessions/    # Currently active sessions
├── branches/           # Branch-specific data
├── archive/            # Completed sessions
└── README.md           # Structure documentation
```

## Identity Check Trigger

The project CLAUDE.md MUST include:

```grip
session.start⟿ identity.check ⫶ load(instance.next_up)
identity.unclear⟿ AskUserQuestion("Who am I speaking with?")
identity.options⟿ J>>(Jane) | M>>(Mike) | S>>(Sara) | A>>(Alex) | K>>(Kim)
```

## Auto-Detection

On session start, if `{project}/.claude/CLAUDE.md` doesn't exist:
1. Prompt: "This project isn't initialised for GRIP. Initialise now?"
2. If yes, create structure with project-appropriate defaults

## Usage

```bash
# Manual invocation
/init-grip

# With project name
/init-grip --name "ProjectName"
```

## Files Created

| File | Purpose |
|------|---------|
| `.claude/CLAUDE.md` | Project rules, identity trigger |
| `.claude/next_up.template.md` | Session state template |
| `~/.claude/projects/{path}/grip/` | Session storage structure |

## Example

### Initialising New Project

```text
$ cd ~/projects/my-new-app
$ claude

[SESSION START]
Project not initialised for GRIP.

/init-grip

Creating GRIP infrastructure:
  ✓ .claude/CLAUDE.md
  ✓ .claude/next_up.template.md
  ✓ ~/.claude/projects/-Users-you-projects-my-new-app/grip/

GRIP initialised for my-new-app.
Session tracking enabled.
```

## Origin

Gen 194: Pattern discovered during internal project setup.
YSH: Pattern proven on first occurrence - immediate abstraction.


