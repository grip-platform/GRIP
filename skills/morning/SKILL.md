---
name: morning
description: Unified start-of-day brief — today's calendar, remaining work, and unread Slack digest in one read.
version: 1.0.0
status: Subsumed
triggers:
  - "/morning"
  - "morning brief"
  - "start of day"
  - "what's on today"
  - "what do I have today"
  - "catch me up for today"
---

# /morning — subsumed

> **Subsumed into `/update-team --morning` (canonical).**
> Start-of-day brief: today's calendar + remaining work + unread Slack digest,
> in one read. Sub-modes `--cal` / `--work` / `--slack`.
>
> The engine is unchanged — calendar via `skills/gcal/gcal.py`, remaining work
> from `rules/session-context.md`, Slack digest from the two most-active
> CodeTonight channels (`#daily-chat` `C0EXAMPLE01`, `#grip-updates`
> `C0EXAMPLE01`). Until the `/update-team --morning` router is wired, the
> calendar step runs directly (so the capability is never orphaned):
>
> ```bash
> python3 ~/.claude/skills/gcal/gcal.py <operator_email> --days 1
> ```
>
> Full execution protocol + design notes retained in git history (reversible,
> archive-never-delete).
