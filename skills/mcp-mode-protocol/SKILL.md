---
name: mcp-mode-protocol
description: Mode management protocol for Web/Mobile surfaces via MCP tools
status: Active
effort: medium
version: 1.0.0
priority: PARAMOUNT
triggers:
  - grip_bootstrap returns surface_hint = mcp_only
  - User requests mode switch on Web/Mobile
  - Any grip_mode_* tool called
integrates:
  - mcp-surface-protocol
  - asking-users
  - creating-wizards
  - hitl
  - context-gate
  - concise-communication
---

# MCP Mode Protocol

**PARAMOUNT**: Follow this protocol when managing modes on Web/Mobile (surface_hint: "mcp_only").

## Available Tools

| Tool | Purpose | State Change |
|------|---------|-------------|
| `grip_mode_list` | List all modes with descriptions and skills | Read-only |
| `grip_mode_activate` | Activate 1-3 modes, returns full skill content | Sets active modes |
| `grip_mode_status` | Show current active modes and loaded skills | Read-only |
| `grip_mode_deactivate` | Clear all active modes | Clears state |

## Activation Workflow

1. User requests a mode (e.g., "switch to code mode", "/mode code")
2. Call `grip_mode_activate` with the mode name(s)
3. Response contains:
   - Foundation skill content (always included)
   - Mode-specific skill content (loaded inline)
   - Prompt injections (mode instructions to follow)
   - Broly configuration (escalation hints)
4. Follow all prompt_injections and loaded skill instructions
5. Confirm to user: "Mode activated: {name}. Skills loaded: {list}."

## Key Rules

### Max 3 Modes Simultaneous

Modes can stack (e.g., "code + security") but maximum 3 at once. Activation replaces all previous modes.

### Foundation Skills Always Active

Foundation skills (asking-users, context-gate, concise-communication) are included in every activation response. They cannot be disabled.

### Skill Content is Inline

Unlike Claude Code (where skills load from filesystem), on Web/Mobile skill content is returned directly in the activation response. Follow the loaded content as instructions.

### Not Mechanically Enforced

Be transparent: mode rules on Web/Mobile are loaded via MCP tool responses, not enforced by hooks or filesystem gates. The AI must self-enforce the loaded skill instructions.

### Check Status Before Activating

If unsure which modes are active, call `grip_mode_status` first.

### Deactivation

Call `grip_mode_deactivate` to clear all modes. Foundation skills remain conceptually active from the bootstrap response.

## Mode Discovery

When user asks "what modes are available?":
1. Call `grip_mode_list`
2. Present as a readable table
3. Highlight recommended mode based on conversation context

When user says `/mode` without a name:
1. Call `grip_mode_list`
2. Present options
3. Ask which mode to activate

## Integration with Surface Protocol

This skill extends `mcp-surface-protocol`. On Web/Mobile:
- Modes activate via MCP tools (not bash commands)
- Skill content delivered inline (not read from filesystem)
- All mcp-surface-protocol rendering rules still apply
