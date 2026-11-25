---
description: Focus Cards -- Kanban-style session tracking with checklists and progress bars.
disable-model-invocation: false
---

# /focus -- Focus Cards

Track what you're working on with Kanban-style cards, checklists, and progress bars.

## Usage

```bash
/focus                               # Show the board (ASCII Kanban)
/focus show CARD_ID                  # Card detail with full checklist
/focus create "Fix auth bug"         # Create a new card for this session
/focus add CARD_ID "Review logic"    # Add a checklist item
/focus done CARD_ID ITEM_ID          # Mark item as done
/focus pin CARD_ID                   # Pin for cross-session persistence
/focus complete CARD_ID              # Mark card as done
/focus stats                         # Summary statistics
```

## Implementation

Run the Focus Cards CLI directly:

```bash
python3 ~/.claude/lib/focus_cards.py board
python3 ~/.claude/lib/focus_cards.py show CARD_ID
python3 ~/.claude/lib/focus_cards.py create --title "TITLE"
python3 ~/.claude/lib/focus_cards.py add CARD_ID "ITEM TEXT"
python3 ~/.claude/lib/focus_cards.py done CARD_ID ITEM_ID
python3 ~/.claude/lib/focus_cards.py pin CARD_ID
python3 ~/.claude/lib/focus_cards.py complete CARD_ID
python3 ~/.claude/lib/focus_cards.py stats
```

## Behaviour

When invoked without arguments, show the ASCII Kanban board.
When invoked with `create`, auto-detect the current git branch and session ID.
When invoked with `show`, render the full card detail with checklist progress.

The MCP App integration renders the board as a `grip_kanban` component with colour-coded columns.

## When to Use

- At session start to see active work
- During a session to track progress
- Before session end to capture remaining items
- For demos to show structured session tracking
