---
description: Show the GRIP changelog — new entries since you last looked, or the full history with --all.
disable-model-invocation: false
---

# /view-changelog — On-Demand Changelog Viewer

Renders `CHANGELOG.md` to the terminal. By default shows only entries newer
than your last view; passes a hint when there is nothing new.

## Usage

```bash
/view-changelog                          # New entries since last view (auto-marks seen)
/view-changelog --all                    # Full history (does NOT mark seen)
/view-changelog --since 2026-04-01       # Entries after a specific date
/view-changelog --no-mark                # Render new entries WITHOUT marking seen
```

## What It Does

1. Reads `~/.claude/CHANGELOG.md` and parses date-headed entries
   (`## YYYY-MM-DD` ... `### Section` ... `- bullet`).
2. Compares against `state/changelog/last-shown.json` (per-machine).
3. Renders entries with word-wrap to the terminal width.
4. Auto-marks the seen-pointer to current head (default mode only).

## Why this exists

Companion to the splash changelog notice — when `bin/grip` shows
"Hooray! GRIP has been updated to 2026-04-27!" you can run
`/view-changelog` to see the actual entries and the splash will fall silent
on the next launch.

The pattern mirrors `omz changelog` from Oh My Zsh: a discoverable command
for full history paired with a passive update banner that only fires when
there is something new.

## Implementation

Load and execute the view-changelog skill:

```
Skill(skill="view-changelog")
```

Or invoke the underlying module directly:

```bash
$HOME/.claude/venv/bin/python $HOME/.claude/lib/grip_changelog.py show         # default: new
$HOME/.claude/venv/bin/python $HOME/.claude/lib/grip_changelog.py show --all   # full
$HOME/.claude/venv/bin/python $HOME/.claude/lib/grip_changelog.py status      # JSON status
```

## State

`state/changelog/last-shown.json` — single per-machine state file. Format:

```json
{
  "shown_through_date": "2026-04-27",
  "file_hash": "sha256:...",
  "last_viewed_at": "2026-04-28T09:00:00Z"
}
```

Reset by deleting (via `trash`) the state file; next splash launch will
silently re-baseline to the current head.
