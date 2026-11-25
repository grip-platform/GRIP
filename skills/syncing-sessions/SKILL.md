---
name: syncing-sessions
description: File-based cross-session context sharing. Concurrent Claude Code sessions write and read transfer payloads via ~/.claude/sync/ directory.
invocable_name: sync
triggers:
  - /sync
aliases:
  - sync
  - session-sync
---

# Session Sync — Cross-Session Context Sharing

File-based sync layer for concurrent Claude Code sessions. Each session writes
its transfer payload to `~/.claude/sync/`; other sessions read peer files to
gain cross-session context automatically.

## When to Use

- `/sync write` — Manually write session context to sync directory
- `/sync read` — Read what other concurrent sessions have shared
- `/sync merge` — Get a unified view of all peer session contexts
- `/sync status` — Check sync state (own file, peer count, stale cleanup)
- `/sync clean` — Remove stale sync files (>2 hours old)

## Automatic Integration

Session sync runs automatically as part of `/save` — no manual invocation
needed for the write step. The sync file is updated every time you save.

## How It Works

1. On `/save`, the session writes its transfer payload to `sync/{session_id}.md`
2. The payload is built by `grip_transfer.py` (achievements, active work, decisions, memories)
3. Other sessions can read peer files via `read_peers()` or get a merged view via `merge_context()`
4. Stale files older than 2 hours are automatically cleaned on read
5. Section-level deduplication keeps the freshest version of each `##` heading

## CLI

```bash
python3 lib/grip_session_sync.py write
python3 lib/grip_session_sync.py read
python3 lib/grip_session_sync.py merge
python3 lib/grip_session_sync.py status
python3 lib/grip_session_sync.py clean
```

## Stage 2 of Universal Session Sync

| Stage | Method | Status |
|-------|--------|--------|
| 1 | Manual `/transfer` via clipboard | Built (PR #1629) |
| 2 | File-based sync via `~/.claude/sync/` | Built (PR #1648) |
| 3 | Socket-based real-time via WebSocket | Future |
