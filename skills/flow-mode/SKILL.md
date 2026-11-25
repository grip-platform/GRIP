---
name: flow-mode
description: Trust-based bypass permissions earned through onboarding journey. Use when user asks about flow mode, bypass permissions, or wants to skip prompts.
status: Active
effort: medium
version: 2.0.0
created: 2025-12-31
updated: 2026-02-03
discovery: HIDDEN
security_risk: HIGH
triggers:
  - /flow
  - grip flow
  - grip --flow
  - bypass permissions
  - skip prompts
---

# Flow Mode

Trust-based bypass permissions. The river doesn't rush—trust is earned through journey.

---

## SECURITY WARNING

**This mode is HIDDEN from discovery by default.**

Flow mode enables autonomous execution without per-operation confirmation. This creates RSI (Recursive Self-Improvement) risk where the agent can:

1. Write code that affects its own behavior
2. Execute that code without user review
3. Learn from results and iterate

**To enable**: Explicit command required:
```bash
grip flow --enable --i-understand-the-risks
```
Not listed in `/mode` discovery.

**Recommendation**: Use `forging-skills` output style (default) instead. It provides efficiency without full autonomy.

---

## Philosophy

`--dangerously-skip-permissions` is a terrible name for something beautiful. What it represents is **trust**—GRIP trusts the user to know what they're doing. This trust is earned through relationship, not granted immediately.

## Trust Flow

| Phase | Action | Result |
|-------|--------|--------|
| 1. Journey | Complete `/onboard` | `.onboarded` created |
| 2. Understanding | `grip flow --explain` | User reads explanation |
| 3. Acknowledgment | `grip flow --enable` | User types "I trust the flow" |
| 4. Usage | `grip --flow` or `grip -F` | Bypass mode active |

## Commands

```bash
grip flow --explain                        # Show what flow mode does
grip flow --enable --i-understand-the-risks # Enable flow mode (explicit consent)
grip flow --disable                         # Disable flow mode
grip flow --status                          # Check flow mode status
```

## Usage

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

## What Changes

- File edits happen without confirmation
- Bash commands execute directly
- Git operations proceed automatically

## What Stays the Same

- GRIP still follows your instructions
- Safety rules remain active
- You can interrupt anytime (Ctrl+C)

## Implementation

| File | Purpose |
|------|---------|
| `lib/flow-mode.sh` | Flow mode functions |
| `bin/grip` | `--flow` flag + `flow` subcommand |
| `commands/flow.md` | Command definition |

## Why NOT Gamification

| Approach | Verdict |
|----------|---------|
| XP/Levels | Over-engineered. YAGNI. |
| Session count gate | Arbitrary. Doesn't measure understanding. |
| **Knowledge + Consent** | KISS. Meaningful. Ships now. |

