# GRIP Team Kanban

The canonical team board for delegation and project management. One
file-backed store, three surfaces: terminal markdown, a one-line summary,
and the interactive `grip_kanban` MCP app.

## Quickstart

```bash
grip-kanban seed                          # one-time: load the open-items snapshot
grip-kanban show                          # render the board as markdown
grip-kanban summary                       # backlog 3 | todo 11 | doing 0 | ...
grip-kanban add "Fix relay flake" --owner A>> --lane todo --link "#3001"
grip-kanban move k003 doing               # pull a card into work
grip-kanban assign k003 A>>               # hand it to Andre
```

`seed` is idempotent — running it twice adds nothing (the issue ref in
each card's links is the dedupe key).

## How team delegation works

1. **Capture** — anything delegatable becomes a card: `grip-kanban add
   "title" --link "#issue"`. Default owner is `GRIP` (unassigned pool).
2. **Delegate** — `grip-kanban assign <id> <owner>` hands the card to a
   team member. Owners are the team signatures plus standing principals:
   `V>>`, `K>>`, `A>>`, `GRIP`.
3. **Track** — owners pull cards across lanes: `backlog → todo → doing →
   review → done`. Every move is appended to the card's `moved_log`, so
   the full lane history is auditable (who-moved-when reconstruction).
4. **Review** — `grip-kanban show` in any session, or project the board
   into a conversation with the grip-apps `grip_kanban` MCP tool via
   `lib/kanban.py::to_grip_apps()` (the payload shape is schema-matched
   to `lib/mcp_apps/sprint.py::generate_sprint_kanban`, enforced by a
   parity test).

The store is plain JSON, so any session — interactive, relay-driven, or
autonomous loop — can read and update the same board concurrently:
writes are atomic (tempfile + `os.replace`), readers always see a
complete board, never a torn write.

## Schema

Store: `state/kanban/board.json` (gitignored runtime state; honours the
`CLAUDE_DIR` env override per `rules/path-portability.md`).

```json
{
  "version": 1,
  "columns": ["backlog", "todo", "doing", "review", "done"],
  "cards": [
    {
      "id": "k001",
      "title": "Slack relay inbound verify",
      "owner": "GRIP",
      "lane": "todo",
      "detail": "",
      "links": ["#1723"],
      "created": "2026-06-10T02:00:00Z",
      "updated": "2026-06-10T02:00:00Z",
      "moved_log": [
        {"at": "2026-06-10T02:00:00Z", "from": null, "to": "todo"}
      ]
    }
  ]
}
```

| Field | Meaning |
|-------|---------|
| `id` | Sequential card id (`k001`, `k002`, ...) |
| `title` | Short imperative description |
| `owner` | Team signature responsible for the card |
| `lane` | Current column (validated against `columns`) |
| `detail` | Optional longer context |
| `links` | Issue/PR refs (`#1723`); first ref doubles as the seed dedupe key |
| `created` / `updated` | UTC timestamps |
| `moved_log` | Append-only lane audit trail (`from: null` = birth lane) |

## Python API

```python
from kanban import (
    load_board, save_board, add_card, move_card, assign_card,
    board_summary, render_markdown, to_grip_apps, seed_board,
)

board = load_board()
card = add_card(board, "Wire the dashboard", owner="K>>", lane="todo")
move_card(board, card["id"], "doing")
save_board(board)
```

All mutating helpers raise `ValueError` on unknown lanes, owners, or
card ids — the CLI surfaces these as one-line errors with exit code 1.

## Tests

```bash
venv/bin/pytest tests/test_kanban.py
```

Covers CRUD round-trip, atomic-write routing (spied `os.replace`) and
crash-intactness, moved_log audit appends with lane-membership Goodhart
anchors, markdown rendering, grip-apps schema parity against the
canonical generator, seed idempotency, and CLI end-to-end via a
temporary `CLAUDE_DIR`.
