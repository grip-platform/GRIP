# /grip-inspect

Inspect and report all active GRIP infrastructure in the current session.

## Usage

```
/grip-inspect
```

## Implementation

Read these files and compile a structured report:

1. **Rules**: List all `.md` files in `~/.claude/rules/` (excluding `CLAUDE.md` index). Mark present/missing against the expected baseline set: `efficiency-rules`, `bash-safety`, `commit-standards`, `session-protocol`, `deployment-security`, `python-async-safety`, `system-capabilities`, `operational-principles`, `path-portability`
2. **Instruction files**: Check `~/.claude/CLAUDE.md` (global), `$PWD/.claude/CLAUDE.md` (project), and project memory (`MEMORY.md`)
3. **Gates**: Parse `~/.claude/config/default-gates.yaml` - show each gate's `id`, `priority`, `action`, and `enabled` status
4. **Hooks**: Parse the `hooks` section of `~/.claude/settings.json` - show event type and script filename for each hook
5. **Mode**: Read `~/.claude/.active-mode` or `.active-modes`, show current mode name. Read `~/.claude/modes/foundation.md` for foundation skills. Read the active mode YAML from `~/.claude/modes/definitions/` for mode-specific skills
6. **Statusline**: Read `~/.claude/config/statusline.yaml` and list active segments

Format as a clean text report using this structure:

```
GRIP Infrastructure Report
===========================

RULES (N files in ~/.claude/rules/)
  [ok] rule-name.md          Description from first heading
  [!!] missing-rule.md       MISSING

INSTRUCTION FILES
  [ok] ~/.claude/CLAUDE.md (global)
  [ok] .claude/CLAUDE.md (project)
  [ok] project memory (MEMORY.md)

GATES (N active in config/default-gates.yaml)
  #0   gate-id               priority:N   action:allow/deny

HOOKS (N event types)
  EventType:  script-name.py, script-name.sh

MODE: mode-name
  Foundation skills: skill1, skill2, ...
  Mode skills: skill1, skill2, ...

STATUSLINE SEGMENTS
  1. grip-indicator  2. directory  3. git  ...

To add rules:  cp your-rule.md ~/.claude/rules/
To add gates:  edit ~/.claude/config/default-gates.yaml
To customize statusline: edit ~/.claude/config/statusline.yaml
```

Use `[ok]` for present items, `[!!]` for missing items.
