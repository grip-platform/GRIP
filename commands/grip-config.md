# /grip-config

GRIP configuration wizard for managing settings interactively.

## Usage

```
/grip-config
```

## Implementation

Present an AskUserQuestion with these categories:

**Question**: "What would you like to configure?"

**Options**:

1. **Statusline** - Configure which segments appear and their order
   - Read `~/.claude/config/statusline.yaml`
   - Show current segments
   - Let user toggle segments on/off or reorder them
   - Write updated config back

2. **Rules** - Manage active rules in `~/.claude/rules/`
   - List current rule files with their first heading as description
   - Options: add new rule (create template `.md` file), disable rule (rename to `.md.disabled`), re-enable disabled rule
   - Show any `.md.disabled` files as available for re-enabling

3. **Gates** - Manage safety gates in `config/default-gates.yaml`
   - List current gates with priority and action
   - Toggle `enabled` field on individual gates
   - Show gate descriptions to help user decide

4. **Modes** - View and switch operating modes
   - List available modes from `~/.claude/modes/definitions/`
   - Show current active mode from `~/.claude/.active-mode` or `.active-modes`
   - Delegate switching to `/mode <name>` command

For each category, use AskUserQuestion to guide the user through changes step by step. After making changes, suggest running `/grip-inspect` to verify the new state.
