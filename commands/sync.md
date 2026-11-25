---
description: Cross-session context sharing via file-based sync.
disable-model-invocation: false
---

# /sync -- Session Sync

Share context between concurrent Claude Code sessions automatically.

## Usage

```bash
/sync                  # Show sync status
/sync write            # Write session context to sync directory
/sync read             # Read peer session contexts
/sync merge            # Get unified view of all peers
/sync clean            # Remove stale files (>2h old)
```

## Implementation

Load and execute the syncing-sessions skill:

```
Skill(skill="syncing-sessions")
```

Or run the module directly:

```bash
python3 lib/grip_session_sync.py status
python3 lib/grip_session_sync.py write
python3 lib/grip_session_sync.py merge
```

## Notes

Session sync runs automatically as part of `/save` -- no manual write needed.
Stale files (>2 hours) are cleaned on every read.
