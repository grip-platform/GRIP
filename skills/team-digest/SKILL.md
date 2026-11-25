---
name: team-digest
description: Subsumed into /update-team --digest (canonical). Inbound per-member Slack channel-activity digest — each member gets others' messages only (self-excluded) with deep links. Engine unchanged (scripts/daily-team-digest.py); run directly until the --digest router is wired.
version: 1.0.0
effort: medium
category: automation
status: Subsumed
triggers:
  - team digest
  - daily digest
  - end of day digest
  - channel summary
dependencies:
  - SLACK_BOT_TOKEN in ~/.claude/mcp-servers/grip-mcp-desktop/.env
  - Team config in ~/.claude/data/team-digest/teams/*.json
token_budget: 500
---

# Team Digest — subsumed

> **Subsumed into `/update-team --digest` (canonical).**
> Inbound per-member Slack channel-activity digest: each member receives only
> others' messages (self-excluded), with clickable deep links to the original
> conversation.
>
> The engine is unchanged — `scripts/daily-team-digest.py`, per-team configs at
> `~/.claude/data/team-digest/teams/*.json`, scheduled via launchd at 22:00.
> Until the `/update-team --digest` router is wired, run the engine directly (so
> the capability is never orphaned):
>
> ```bash
> python3 ~/.claude/scripts/daily-team-digest.py --dry-run
> python3 ~/.claude/scripts/daily-team-digest.py --team codetonight
> ```
>
> Full configuration + troubleshooting reference retained in git history
> (reversible, archive-never-delete).
