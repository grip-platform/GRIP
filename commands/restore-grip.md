---
name: restore-grip
description: Restore GRIP from backup
---

# /restore-grip

Restores GRIP infrastructure from a backup archive.

## Usage

```bash
/restore-grip <backup.tar.gz> [--force]
```

## Options

- `--force` - Overwrite without conflict prompts

## Conflict Handling

When local is newer than backup, prompts:

1. Replace (lose local changes)
2. Abort
3. Backup local first, then restore

## Security

`.env` files are NEVER restored. Recreate manually after restore.

## Implementation

Runs: `~/.claude/scripts/grip-restore.sh`
