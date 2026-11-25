---
name: view-changelog
description: Render GRIP CHANGELOG.md entries — new since last view by default, full history with --all, or filtered by --since date. Pairs with the bin/grip splash "Hooray!" notice. Trigger when the user types /view-changelog or asks "what's new", "show changelog", "what changed in GRIP", "GRIP updates".
triggers:
  - /view-changelog
  - what's new in GRIP
  - show changelog
  - GRIP changelog
  - what changed
---

# view-changelog — On-Demand Changelog Viewer

Companion to the bin/grip splash changelog notice. Where the splash silently
shows a one-line "Hooray! GRIP has been updated!" banner when there is new
content, this skill renders the actual entries on demand.

## When to Use

- User typed `/view-changelog` (any flag)
- User asked "what's new in GRIP", "show me the changelog", "what changed",
  "what features shipped recently"
- The splash "Hooray!" notice prompted them to dig in

## What to Do

Invoke the canonical Python module — it's the single source of truth shared
with the splash and the SessionStart hook.

### Default: render new entries since last view

```bash
$HOME/.claude/venv/bin/python $HOME/.claude/lib/grip_changelog.py show
```

This auto-marks the seen-pointer on success, so the splash banner falls
silent on the next launch.

### Full history

```bash
$HOME/.claude/venv/bin/python $HOME/.claude/lib/grip_changelog.py show --all
```

Does NOT mark seen — useful for archaeology without disturbing the
new-content signal.

### Since a specific date

```bash
$HOME/.claude/venv/bin/python $HOME/.claude/lib/grip_changelog.py show --since 2026-04-01
```

Date format: `YYYY-MM-DD`. Entries strictly after the given date.

### Render WITHOUT marking seen (preview)

```bash
$HOME/.claude/venv/bin/python $HOME/.claude/lib/grip_changelog.py show --no-mark
```

### JSON status (for automation)

```bash
$HOME/.claude/venv/bin/python $HOME/.claude/lib/grip_changelog.py status
```

Returns: `{"has_new": bool, "count": int, "summary": str, "newest_date": str|null}`.

## Output Style

The module renders word-wrapped, indented entries grouped by date and
section. No GRIP jargon — Plain Language (Rule 17). Section names are
preserved verbatim from CHANGELOG.md (Features, Fixes, Documentation, …).

## Falsifier

This skill is wrong if:
- It surfaces fewer entries than `git log -- CHANGELOG.md` since last view
  (parser drift), OR
- It marks seen on `--all` or `--since` (which would silence the splash
  banner unintentionally — these flags are explicitly archive-mode), OR
- Operators report they couldn't discover this command from the splash
  banner (discoverability backfire — the "/view-changelog" tagline failed).

Track via:
- `tests/test_grip_changelog.py` for parser correctness
- Operator feedback in `learning/insights.jsonl`
