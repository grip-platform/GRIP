---
name: slack-feature-updates
description: Generate and post Block Kit feature updates to the CodeTonight Slack workspace. Auto-discovers PRs/commits/issues for a given period and formats them as rich, categorised updates.
status: Deprecated
effort: medium
version: 1.0.0
created: 2026-03-11
triggers:
  - /slack-feature-updates
  - "post feature update"
  - "team update slack"
  - "what shipped this week"
integrates:
  - styling-slack-blocks
  - pr-automation
priority: STANDARD
---

# Slack Feature Updates — subsumed

> **Subsumed into `/update-team` (canonical, default mode).**
> - Scheduled no-spam team update → **`/update-team`**.
> - One-shot on-demand update → **`/aurora`**.
>
> The PR-discovery + categorisation + Block Kit rendering this skill did now
> live in `lib/work_digest.py` + `lib/aurora_report.py` + `lib/comms_update.py`;
> the transport path is `lib/slack_transport.py::post(workspace, channel_id,
> text, blocks=...)`. This skill's full protocol is retained in git history
> (reversible, archive-never-delete).
