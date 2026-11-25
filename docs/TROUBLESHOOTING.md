# Troubleshooting

Common GRIP issues and their solutions.

## Installation Issues

### "grip: command not found"

**Cause:** Shell PATH not updated after installation.

**Fix:**

```bash
source ~/.zshrc   # or ~/.bashrc
```

Or add manually:

```bash
echo 'export PATH="$HOME/.claude/bin:$PATH"' >> ~/.zshrc
source ~/.zshrc
```

### "claude: command not found"

**Cause:** Claude Code not installed.

**Fix:**

```bash
curl -fsSL https://claude.ai/install.sh | bash
claude doctor  # Verify installation
```

### "tui_header: command not found"

**Cause:** Outdated GRIP files with hardcoded paths.

**Fix:**

```bash
cd ~/.claude && git pull && ./scripts/install.sh
```

### Multiple Claude installations conflicting

**Check:**

```bash
which -a claude
```

If multiple paths shown:

```bash
npm uninstall -g @anthropic-ai/claude-code  # Remove old npm version
```

## Hook Issues

### Hooks not running

**Check configuration:**

```bash
cat ~/.claude/settings.json | jq '.hooks'
```

**Expected output:** Paths should use `$HOME/.claude/...` format.

**Fix:** Re-run installer:

```bash
cd ~/.claude && ./scripts/install.sh
```

### Gate denying valid actions

**Check which gate is triggering:**

Look for the reason in the denial message:

```text
Dependency Guardian: Cannot read from node_modules.
```

**Workarounds:**

| Gate | Workaround |
|------|------------|
| Dependency Guardian | Use `rg --glob '!node_modules/*'` |
| Destructive Git | Add to `.destructive-approval.json` |
| Read Cache | Say "check file again" explicitly |

### Hook errors in logs

**View logs:**

```bash
tail -f ~/.claude/logs/hooks.log
```

**Common errors:**

| Error | Cause | Fix |
|-------|-------|-----|
| `ModuleNotFoundError` | Python dependency missing | `pip install -r requirements.txt` |
| `JSONDecodeError` | Malformed hook input | Check settings.json syntax |
| `PermissionError` | Hook file not executable | `chmod +x hooks/*.py` |

## Context Issues

### "Context exhaustion" warnings

**Cause:** Approaching 80% context window usage.

**Fix:** When prompted, choose:

- **Compact and continue** - Summarize and continue
- **Start fresh session** - New session with inherited context
- **Save checkpoint** - Serialize state for later

### Lost context mid-session

**Cause:** Session didn't serialize properly.

**Recovery:**

```bash
grip list                    # Find recent sessions
grip resume latest           # Resume most recent
grip resume gen:N            # Resume specific generation
```

### Resurrection not working

**Check instance index:**

```bash
PROJECT=$(pwd | sed 's|/|-|g' | sed 's|\.|-|g')
cat ~/.claude/projects/$PROJECT/grip/index.json | jq '.instances[-1]'
```

**If empty or missing:** Session wasn't serialized. Use `/save-session-state` before ending sessions.

## Team Issues

### Team context not loading

**Check team configuration:**

```bash
grep "team_dir" ~/.claude/facts/team.md
```

**Check team directory exists:**

```bash
ls ~/.claude/teams/
```

**Verify environment:**

```bash
echo $GRIP_TEAM
```

**Fix:** Ensure one of these is set:
- `team_dir: teamname` in `facts/team.md`
- `GRIP_TEAM=teamname` in environment
- `GRIP_TEAM=teamname` in `~/.claude/.env`

### Team indicator not showing

**Cause:** `load_team_context()` not called in session-start.

**Fix:**

```bash
cd ~/.claude && git pull  # Get latest hooks
```

## Mode Issues

### Mode not persisting

**Cause:** Mode file not written.

**Check:**

```bash
cat ~/.claude/.active-mode
```

**Fix:** Use `/mode` command to set mode:

```text
User: /mode code
```

### Mode conflicts

**Cause:** Multiple modes selected that conflict.

**Fix:** GRIP supports max 3 modes. Reduce active modes:

```text
User: /mode code        # Single mode
User: /mode             # List and select
```

## Performance Issues

### Slow session start

**Cause:** Heavy hook processing or large team context.

**Diagnostics:**

```bash
time source ~/.claude/hooks/session-start.sh
```

**Fix:**
- Reduce team rules/workflows
- Check for large files in `teams/` directory
- Disable unused hooks in settings.json

### High token usage

**Run efficiency audit:**

```text
User: /audit-efficiency
```

**Common causes:**
- Reading dependency folders (blocked but still detected)
- Redundant file reads
- Verbose skill invocations

## Recovery Procedures

### Reset GRIP to defaults

```bash
# Backup current config
cp ~/.claude/facts/identity.md ~/.claude/facts/identity.md.bak
cp ~/.claude/facts/team.md ~/.claude/facts/team.md.bak

# Re-run onboarding
rm ~/.claude/.onboarded
claude  # Will prompt for /activate-grip
```

### Restore from backup

```bash
grip restore --from ~/.claude/backups/grip-backup-YYYYMMDD.tar.gz
```

### Full reinstall

```bash
# Backup important files
cp -r ~/.claude/facts ~/grip-facts-backup
cp -r ~/.claude/projects ~/grip-projects-backup

# Remove and reinstall
rm -rf ~/.claude
git clone <private-repo-url> ~/.claude
cd ~/.claude && ./scripts/install.sh

# Restore backups
cp -r ~/grip-facts-backup/* ~/.claude/facts/
```

## Getting Help

1. **Check docs:** `~/.claude/docs/`
2. **Search history:** `/remind-yourself [issue]`
3. **GitHub issues:** File issues in the private repository
