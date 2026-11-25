# Focus Cards Architecture

Session-to-card mapping for Claude Code. Maps active sessions to Kanban-style
cards with auto-inferred checklists.

## Data Flow

```
SessionStart hook
    |
    v
hooks/focus-cards-hook.py start   (on_start)
    |-- resume this session's card, or a paused or pinned card on the branch
    |-- otherwise FocusInference + FocusCardsManager.create_card()
    v
.focus-cards-state.json  <-- atomic_write_json (crash-safe)
    |
    v
PostToolUse hook -- acts on Bash calls only
    |
    v
hooks/focus-cards-hook.py post   (on_post)
    |-- COMMIT_PATTERN on the tool output
    |-- FocusInference.from_commit_message()
    |-- close a matching pending item, or record the commit as a done item
    |-- FocusCardsManager.save()
    v
.focus-cards-state.json  <-- updated with new items
    |
    v
MCP Server (TypeScript) -- reads state file
    |-- focus_cards_board -> grip_kanban UI
    |-- focus_cards_get -> card detail
    |-- focus_cards_complete_item -> mark done
    |-- focus_cards_pin -> cross-session persist
    |-- focus_cards_stats -> summary
    v
VS Code / Cowork UI

SessionEnd hook
    |
    v
hooks/focus-cards-hook.py end   (on_end)
    |-- Auto-complete if all items done
    |-- Pause if session ending with work remaining
    |-- Pinned cards persist unchanged
    v
.focus-cards-state.json  <-- final save
```

## Module Map

| Module | Language | Role |
|--------|----------|------|
| `lib/focus_cards.py` | Python | Data model + Manager (CRUD, persistence) |
| `lib/focus_inference.py` | Python | Heuristic inference engine |
| `hooks/focus-cards-hook.py` | Python | Hook integration layer (`start`, `post`, `end`) |
| `lib/focus_cards_hooks.py` | Python | Legacy hook layer, unwired and kept only for its tests: never register it |
| `mcp-servers/grip-mcp-web/src/focus-cards.ts` | TypeScript | MCP tool definitions |

## State File

Location: `~/.claude/.focus-cards-state.json`

```json
{
  "version": 1,
  "updated_at": "2026-02-24T...",
  "cards": [
    {
      "id": "abc123",
      "session_id": "session-uuid",
      "title": "Focus cards phase1",
      "status": "active",
      "branch": "feat/focus-cards-phase1",
      "checklist": [
        { "id": "x1", "text": "Add hook integration", "status": "done" },
        { "id": "x2", "text": "Write MCP tools", "status": "pending" }
      ]
    }
  ]
}
```

## Card Lifecycle

```
ACTIVE --> PAUSED      (session end, work remaining)
ACTIVE --> COMPLETED   (all checklist items done)
ACTIVE --> PINNED      (user pins for cross-session persistence)
PAUSED --> ACTIVE      (session resumes same session_id)
PINNED --> PINNED      (survives session boundaries)
*      --> ARCHIVED    (user archives)
```

## Hook Registration

The focus-cards hooks are not wired by default. `config/hooks.registry.json`
marks `policy.focus-cards-hook-py` as `intentionally-inactive`, and the tracked
`settings.json` carries no focus-cards binding.

The supported entry point is `hooks/focus-cards-hook.py`, with one argument per
event:

| Event | Matcher | Argument | What it does |
|-------|---------|----------|--------------|
| `SessionStart` | all | `start` | resumes this session's card, or a paused or pinned card on the branch; otherwise creates one |
| `PostToolUse` | `Bash` | `post` | each commit in the tool output closes a matching pending item or is recorded as a done item |
| `SessionEnd` | all | `end` | completes the card when every item is done, pauses it otherwise, and leaves a pinned card pinned |

Run it through GRIP's Python wrapper, as the other bound hooks are. The
`PostToolUse` command, for example:

```json
{
  "type": "command",
  "command": "bash \"${CLAUDE_DIR:-$HOME/.claude}/lib/grip-python.sh\" \"${CLAUDE_DIR:-$HOME/.claude}/hooks/focus-cards-hook.py\" post"
}
```

Never register `lib/focus_cards_hooks.py`. Its own header says it was not given
the #2885 completion fix, so a checklist it builds stays pending forever.
`tests/test_focus_cards_p2.py` fails if the shipped `settings.json` wires it.

## Inference Sources

| Source | Confidence | Pattern |
|--------|-----------|---------|
| Commit messages | 0.8 | `feat:`, `fix:`, `TODO:`, `WIP:` |
| Plan checkboxes | 0.7 | `- [ ] text`, `1. text`, `## Step N:` |
| Git diff stats | 0.5 | Files with >20 line changes |
| Branch names | 0.8 | `feat/`, `fix/` prefix extraction |

## Phases

- **Phase 1** (this): Core data model + hooks + tests
- **Phase 2** (this): MCP server tools + VS Code integration
- **Phase 3** (future): KONO memory integration for semantic recall
