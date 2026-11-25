# Custom GRIP Statusline

Enable a rich, configurable statusline in Claude Code that shows git status, active modes, context usage, and more.

## Quick Start

### 1. Enable the statusline in settings

Add to `~/.claude/settings.json`:

```json
{
  "statusLine": {
    "type": "command",
    "command": "bash $HOME/.claude/statusline-command.sh"
  }
}
```

Claude Code pipes JSON workspace context (containing `workspace.current_dir`) to stdin of this command. The script outputs ANSI-colored text rendered as the statusline.

### 2. Create the statusline script

Create `~/.claude/statusline-command.sh`. This script:

- Reads JSON from stdin to get the current working directory
- Renders segments (git, mode, model, etc.) as colored text
- Outputs a single line of ANSI-formatted text

Minimal example:

```bash
#!/usr/bin/env bash
input=$(cat)
dir=$(echo "$input" | jq -r '.workspace.current_dir')
cd "$dir" 2>/dev/null || exit 1

G='\033[1;32m' C='\033[36m' R='\033[31m' B='\033[1;34m' Z='\033[0m'

# Directory
segment_dir=$(printf "${G}->  ${C}%s${Z}" "$(basename "$dir")")

# Git branch
segment_git=""
if git rev-parse --git-dir >/dev/null 2>&1; then
    branch=$(git symbolic-ref --short HEAD 2>/dev/null || echo "detached")
    segment_git=$(printf "${B}git:(${R}%s${B})${Z}" "$branch")
fi

printf "%s | %s" "$segment_dir" "$segment_git"
```

### 3. Configure segments (optional)

Create `~/.claude/config/statusline.yaml` to control which segments render and in what order:

```yaml
# Comment out or remove segments to hide them. Reorder to change position.
segments:
  - grip-indicator    # "GRIP" label
  - directory         # current dir name
  - git               # branch, ahead/behind, staged/modified/untracked
  - mode              # active mode(s) + skill abbreviations
  - broly             # bro:level(hint)
  - config-health     # context bar + R:loaded/expected G:gates
  - forge-ops         # ops:current/threshold
  - model             # opus:1M | son:1M
  - context           # ctx:remaining%
  - rsi               # rsi:ready/pending
  - hints             # skill suggestions
```

Remove a line to hide that segment. Reorder lines to change position.

## Segment Reference

| Segment | Output Example | Source |
|---------|---------------|--------|
| `grip-indicator` | `GRIP` | Checks `~/.claude/CLAUDE.md` exists |
| `directory` | `-> .claude` | `basename` of working dir |
| `git` | `git:(main) ~5?6` | git status (staged/modified/untracked) |
| `mode` | `code[feature,design]` | `~/.claude/.grip-mode-state.json` |
| `broly` | `bro:fork(cot)` | BROLY agent level from mode YAML |
| `config-health` | `[====] R:12/12 G:11` | Counts loaded rules + gates |
| `forge-ops` | `ops:0/12` | `~/.claude/.forge-state.json` |
| `model` | `opus:1M` | `~/.claude/.model-state.json` |
| `context` | `ctx:21%` | `~/.claude/.context-gate-state.json` |
| `rsi` | `rsi:3/5` | `~/.claude/.rsi-state.json` |
| `hints` | `hint:testing` | `~/.claude/.skill-suggestions.json` |

## Git Status Symbols

The `git` segment uses compact notation:

| Symbol | Meaning |
|--------|---------|
| `~N` | N staged files |
| `!N` | N modified (unstaged) files |
| `?N` | N untracked files |
| `*N` | N stash entries |
| `up N` | N commits ahead of upstream |
| `dn N` | N commits behind upstream |

## Colors

The script uses ANSI escape codes:

| Color | Code | Used For |
|-------|------|----------|
| Green | `\033[1;32m` | GRIP indicator, directory arrow |
| Cyan | `\033[36m` | Directory name |
| Red | `\033[31m` | Branch name, warnings |
| Yellow | `\033[33m` | Ahead/behind counts, untracked |
| Blue | `\033[1;34m` | Git wrapper |
| Magenta | `\033[35m` | Mode names |
| Dim | `\033[2m` | Secondary info |

## Adding Custom Segments

1. Add a `render_my_segment()` function to `statusline-command.sh`
2. Add `my-segment` to `statusline.yaml`
3. Add dispatch case in the segment loop at the bottom of the script

```bash
render_my_segment() {
    printf "${C}custom:data${Z}"
}
```

## Troubleshooting

**Statusline not showing**: Verify `settings.json` has the `statusLine` block and restart Claude Code.

**Segments missing**: Check `statusline.yaml` exists and lists the segments you want.

**No colors**: Ensure your terminal supports ANSI escape codes.

**jq errors**: The script requires `jq` installed (`brew install jq`).
