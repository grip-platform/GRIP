---
name: flow
description: Manage trust-based bypass permissions mode
---

# /flow

Trust-based bypass permissions management. Flow mode lets GRIP work without permission prompts after trust is established through onboarding.

## Philosophy

`--dangerously-skip-permissions` is a terrible name for something beautiful. What it represents is **trust**—GRIP trusts the user to know what they're doing. This trust is earned through relationship, not granted immediately.

## Usage

```bash
grip flow --explain    # Show what flow mode does
grip flow --enable     # Enable flow mode (requires onboarding)
grip flow --disable    # Disable flow mode
grip flow --status     # Check if flow mode is enabled
```

## Using Flow Mode

Once enabled:

```bash
grip --flow            # Start session with flow mode
grip -F                # Short alias
grip --flow fresh 5000 # Flow mode with fresh context
```

## Trust Requirements

Flow mode requires BOTH:

1. **Onboarding complete** (`~/.claude/.onboarded` exists)
2. **Flow enabled** (`~/.claude/.flow-enabled` exists)

## The Trust Flow

| Phase | Action | Result |
|-------|--------|--------|
| 1. Journey | Complete `/onboard` | `.onboarded` created |
| 2. Understanding | Run `grip flow --explain` | User reads explanation |
| 3. Acknowledgment | Run `grip flow --enable` | User types "I trust the flow" |
| 4. Usage | Run `grip --flow` | Bypass mode active |

## What Changes in Flow Mode

- File edits happen without confirmation
- Bash commands execute directly
- Git operations proceed automatically

## What Stays the Same

- GRIP still follows your instructions
- Safety rules remain active
- You can interrupt anytime (Ctrl+C)

## Implementation

Runs: `~/.claude/lib/flow-mode.sh`
