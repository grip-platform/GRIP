# Hooks Architecture

How GRIP enforces safety gates through Claude Code hooks.

> **Authority note (2026-07-30):** this document explains the historical
> Claude-specific architecture. Hook wiring and policy classification now come
> only from `config/hooks.registry.json`; generated views must pass
> `python3 lib/hooks_registry.py generate --check`. Older response examples
> below are explanatory history, not the current hook response contract.
> The harness-agnostic Wave 1 contract is documented in
> [`HOOK-ENGINE.md`](HOOK-ENGINE.md).

## Overview

GRIP uses Claude Code's hook system to intercept tool calls before execution. The main enforcement hook is `context-gate-hook.py`, which evaluates 10 gates for every tool call.

```text
User Request
     │
     ▼
Claude Plans Tool Call
     │
     ▼
PreToolUse Hook Fires ─────────────────┐
     │                                  │
     ▼                                  │
context-gate-hook.py                    │
     │                                  │
     ├── Gate 0: Always Allow ──────────┤
     ├── Gate 1: Dependency Guardian ───┤
     ├── Gate 2: Destructive Git ───────┤
     ├── Gate 3: Secrets Detection ─────┤
     ├── Gate 4: Read Cache ────────────┤
     ├── Gate 5: Bash Tool Substitution─┤
     ├── Gate 6: Bash Linting ──────────┤
     ├── Gate 7: Temp Script Prevention─┤
     ├── Gate 8: Skill Creation ────────┤
     └── Gate 9: Context Gate ──────────┤
                                        │
     ┌──────────────────────────────────┘
     │
     ▼
┌─────────┐     ┌─────────┐
│  ALLOW  │     │  DENY   │
└────┬────┘     └────┬────┘
     │               │
     ▼               ▼
Tool Executes    Blocked + Reason
```

## Hook Configuration

Hooks are configured in `~/.claude/settings.json`:

```json
{
  "hooks": {
    "PreToolUse": [{
      "matcher": ".*",
      "hooks": [{
        "type": "command",
        "command": "python3 $HOME/.claude/hooks/context-gate-hook.py"
      }]
    }]
  }
}
```

## The 10 Gates

### Gate 0: Always Allow

Certain tools are always allowed to prevent blocking user interaction:

- `AskUserQuestion` - User communication
- `TaskCreate`, `TaskUpdate`, `TaskList`, `TaskGet` - Task management
- `ExitPlanMode`, `EnterPlanMode` - Planning tools

### Gate 1: Dependency Guardian

**Tools affected:** `Read`, `Glob`, `Grep`

Blocks reading from dependency folders:

```text
BLOCKED: node_modules, .next, dist, build, __pycache__, venv, target, vendor, Pods
```

**Why:** Reading these folders wastes 50k+ tokens and provides no useful context.

**Example denial:**

```json
{
  "decision": "deny",
  "reason": "Dependency Guardian: Cannot read from node_modules. Use rg --glob '!node_modules/*' instead."
}
```

### Gate 2: Destructive Git

**Tools affected:** `Bash`

Blocks dangerous git operations:

| Pattern | Block Reason |
|---------|--------------|
| `git push --force` | Data loss risk |
| `git push -f` | Data loss risk |
| `git reset --hard` | Uncommitted changes lost |
| `git clean -f` | Untracked files deleted |

**Exception:** User can pre-approve via `.destructive-approval.json`.

### Gate 3: Secrets Detection

**Tools affected:** `Bash`

Blocks committing files that may contain secrets:

| Pattern | Risk |
|---------|------|
| `.env` | Environment variables |
| `.pem`, `.key` | Private keys |
| `credentials.json` | API credentials |
| `secrets.yaml` | Secret configuration |

### Gate 4: Read Cache

**Tools affected:** `Read`

Tracks recently read files and warns on redundant reads:

```text
File read 2 messages ago. Use cached knowledge instead.
```

**Implementation:** LRU cache of last 10 file reads.

### Gate 5: Bash Tool Substitution

**Tools affected:** `Bash`

Enforces modern CLI tools:

| Instead of | Use |
|------------|-----|
| `grep` | `rg` (ripgrep) |
| `find` | `fd` |
| `cat` (for display) | `bat` |

### Gate 6: Bash Linting

**Tools affected:** `Bash`

Warns on dangerous bash patterns:

- Semicolons after `$()` (parse errors in eval)
- Unquoted variables
- Missing `--` before variable paths

### Gate 7: Temp Script Prevention

**Tools affected:** `Write`

Warns when creating temporary scripts:

```text
Creating temp script. Consider using direct tool calls instead.
```

### Gate 8: Skill Creation

**Tools affected:** `Write`, `Edit`

Enforces skill creation best practices when modifying files in `~/.claude/skills/`:

- SKILL.md must follow template
- Required sections must be present
- Token budget must be specified

### Gate 9: Context Gate

**Tools affected:** All

Monitors context window usage and halts at 80%:

```text
CONTEXT GATE: 80% threshold reached. Halting for user decision.
```

**Options presented:**
- Compact and continue
- Start fresh session
- Save checkpoint and end

## Session Hooks

### session-start.sh

Runs at session initialization:

```bash
#!/usr/bin/env bash
source "$HOME/.claude/hooks/startup/core.sh"
source "$HOME/.claude/hooks/startup/grip.sh"
source "$HOME/.claude/hooks/startup/output.sh"

# Initialization sequence
init_core_variables
grip_auto_resurrect
load_team_context
restore_active_mode
emit_status_line
```

**Loading order:**

1. Core variables and paths
2. GRIP resurrection (if previous session exists)
3. Team context (if team configured)
4. Active mode restoration
5. Status line output

## Debugging Hooks

### Check if hooks are configured

```bash
cat ~/.claude/settings.json | jq '.hooks'
```

### Test hook manually

```bash
echo '{"tool_name": "Read", "tool_input": {"file_path": "/path/to/node_modules/test.js"}}' | \
  python3 ~/.claude/hooks/context-gate-hook.py
```

**Expected output:**

```json
{
  "decision": "deny",
  "reason": "Dependency Guardian: Cannot read from node_modules."
}
```

### View hook logs

```bash
tail -f ~/.claude/logs/hooks.log
```

### Common Issues

| Symptom | Cause | Fix |
|---------|-------|-----|
| Hooks not running | settings.json misconfigured | Verify paths use `$HOME` |
| "command not found" | Python not in PATH | Use full path to python3 |
| Gate not triggering | Regex mismatch | Check pattern in hook code |

## Extending Gates

To add a custom gate, edit `context-gate-hook.py`:

```python
def check_custom_gate(tool_name: str, tool_input: dict) -> tuple[str, str]:
    """Custom gate example."""
    if tool_name == "Bash":
        command = tool_input.get("command", "")
        if "dangerous_pattern" in command:
            return "deny", "Custom gate: Blocked dangerous pattern"
    return "allow", ""
```

Add to the gate evaluation chain:

```python
gates = [
    check_always_allow,
    check_dependency_guardian,
    # ... existing gates ...
    check_custom_gate,  # Add custom gate
    check_context_gate,
]
```

## Hook Response Format

All hooks must return JSON:

```json
{
  "decision": "allow" | "deny",
  "reason": "Explanation shown to user (required if deny)"
}
```

**Additional fields:**

```json
{
  "decision": "allow",
  "systemMessage": "Injected into Claude's context"
}
```

The `systemMessage` field allows hooks to inject context without blocking the tool call.
