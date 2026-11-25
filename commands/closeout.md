---
description: Automates session-end checklist including git commit, push, memory save, RSI lock cleanup, and optional Slack summary. Use when ending a session.
disable-model-invocation: false
---

# /closeout -- Session End Automation

Automate the session-end ritual: commit, push, save memory, clean up, summarize.

## Usage

```bash
/closeout                              # Auto-detect, commit, push, save
/closeout "finished auth refactor"     # Explicit achievement message
/closeout --no-push                    # Commit and save but skip push
/closeout --slack C0EXAMPLE01          # Also post digest to Slack
```

## Implementation

Execute the closeout orchestrator:

```bash
python3 ~/.claude/lib/closeout.py $ARGUMENTS
```

## What It Does

1. Check `git status` for uncommitted changes
2. Commit staged changes with a conventional commit message (respects commit-standards.md)
3. Push to current branch (halts on protected branches)
4. Save all GRIP memory subsystems via `grip_save.py`
5. Release RSI lock if held
6. Stop caffeinate if running
7. List outstanding TODOs and open issues
8. Post Slack summary if `--slack` flag provided

## Safety

- Protected branches (main, master, production): requires explicit confirmation before push
- Never force pushes
- Never includes AI attribution in commits
- Each step is isolated; failures are non-blocking

ARGUMENTS: $ARGUMENTS
