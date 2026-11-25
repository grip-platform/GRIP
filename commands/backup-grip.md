---
name: backup-grip
description: Create GRIP infrastructure backup
---

# /backup-grip

Creates a backup of GRIP infrastructure.

## Usage

```bash
/backup-grip [quick|full|complete]
```

## Tiers

| Tier | Size | Contents |
|------|------|----------|
| quick | ~3MB | Config + skills |
| full | ~5MB | All infrastructure (default) |
| complete | ~73MB | Including projects/ |

## Output

```text
~/backups/grip/grip-{tier}-{timestamp}.tar.gz
```

## Implementation

Runs: `~/.claude/scripts/grip-backup.sh`
