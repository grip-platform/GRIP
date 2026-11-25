---
name: tab-manager
description: Cross-platform browser tab management from the CLI. Snapshot, restore, search, close, and garbage-collect tabs across all running browsers. Protects RAM by enabling one-shot tab archival and instant recall.
status: Active
effort: medium
version: 1.0.0
triggers:
  - "/tabs"
  - "manage tabs"
  - "close tabs"
  - "save my tabs"
  - "too many tabs"
  - "browser memory"
  - "tab hoarder"
integrates:
  - tab_manager.py
see_also:
  - optimizing-system-resources
  - node-cleanup
---

# /tabs -- GRIP Tab Manager

One-shot browser tab management from the CLI. Like Toby, but without the extension -- pure AppleScript/PowerShell automation.

## Commands

```bash
/tabs                     # Show all open tabs + memory usage
/tabs snapshot [name]     # Save all tabs to named snapshot
/tabs restore <name>      # Reopen tabs from a snapshot
/tabs close <pattern>     # Close tabs matching URL/title pattern
/tabs gc                  # Dry-run: find duplicates + stale tabs
/tabs gc --force          # Execute: close duplicates + stale tabs
/tabs search <query>      # Search across all saved snapshots
/tabs snapshots           # List all saved snapshots
/tabs memory              # Show per-browser RAM usage
/tabs nuke [browser]      # Snapshot ALL then close ALL (RAM recovery)
```

## Protocol

### When user invokes /tabs (no args)

Show a combined view: open tabs + memory usage.

```bash
python3 "$CLAUDE_DIR/lib/tab_manager.py" list
python3 "$CLAUDE_DIR/lib/tab_manager.py" memory
```

### When user invokes /tabs with a subcommand

Pass directly to the CLI:

```bash
python3 "$CLAUDE_DIR/lib/tab_manager.py" <subcommand> [args...]
```

### Smart defaults

- `/tabs snapshot` without a name auto-generates a timestamp name
- `/tabs restore` does fuzzy matching on snapshot names
- `/tabs nuke` always snapshots before closing (safety net)
- `/tabs gc` is always dry-run unless `--force` is passed

## Cross-Platform Support

| Platform | Browsers Supported | Method |
|----------|-------------------|--------|
| **macOS** | Chrome, Brave, Safari, Edge, Arc, Vivaldi | AppleScript |
| **Windows** | Chrome, Brave, Edge, Firefox, Vivaldi | PowerShell (planned) |
| **Linux** | Limited | xdotool (planned) |

## Storage

Snapshots: `~/.claude/state/tab-snapshots/*.json`

Each snapshot contains:
- Snapshot name and timestamp
- All tab URLs, titles, browser, window/tab index
- Browser list for the session

## Why This Exists

Browsers consume 50-200 MB per tab. A tab hoarder with 40+ tabs across multiple browsers can easily consume 3-5 GB of RAM. This tool enables:

1. **Save memory**: Snapshot and close tabs in one command
2. **Save information**: URLs and titles preserved as searchable JSON
3. **Instant recall**: Restore any snapshot to reopen all tabs
4. **Cross-session search**: Find that tab you had open last week
