---
name: generating-changelogs
description: Generate a changelog from git history. Use when invoking /changelog, preparing release notes, or running the scheduled daily changelog job.
---

# Generating Changelogs

Automated daily changelog generation from git history.

## Trigger

- `/changelog` command
- Scheduled: daily at 23:55 via launchd

## Protocol

Uses broly FORK level (2 agents):

### Agent 1: git-analyzer

1. Run `git log -n 10000 --since="24 hours ago" --oneline --no-merges` in `~/.claude` (keep the `-n`: the RTK hook's rewrite of a plain `git log` stops at 50 commits; GRIP#9179)
2. Categorize commits by conventional type prefix (feat/fix/chore/docs/refactor/test)
3. Group into semantic sections: MCP & UI, Modes, Security, Infrastructure, Documentation, Fixes, Refactoring
4. Pass structured list to Agent 2

### Agent 2: changelog-writer

1. Receive categorized commits from Agent 1
2. Generate condensed markdown with date header and sectioned bullet points
3. Prepend new entry to `~/.claude/CHANGELOG.md` (after the `# Changelog` title block)
4. Preserve all existing entries unchanged

## Format

```markdown
## YYYY-MM-DD

### Section Name
- Condensed description of change
- Another change
```

## Rules

- Never rewrite or modify existing entries
- Skip if zero commits in the last 24 hours
- Merge related commits into single bullet points where appropriate
- Use past tense ("Added", "Fixed", "Refactored")
- Token budget: ~3k per execution

## Wrapper Script

`~/.claude/scripts/daily-changelog.sh` - invoked by launchd agent `com.grip.daily-changelog`

## Registration

- Skill: `generating-changelogs|/changelog|~3k|daily git-to-changelog via broly FORK`
- Command: `/changelog|~3k|-|generate changelog from recent git commits`
