---
name: slack-blocks
description: Slack Block Kit styling patterns for beautiful, structured messages. Use for rich notifications, dashboards, status updates, and formatted reports.
status: Subsumed (render substrate)
effort: high
version: 1.0.0
triggers:
  - slack notification needed
  - rich message formatting
  - dashboard display
  - status update with metrics
integrates:
  - copilot-rituals
  - slack-integration
---

# Slack Block Kit Styling — subsumed (render substrate)

> **Subsumed into `/update-team` (canonical).** For team/update posts use
> **`/update-team`**. These Block Kit patterns are not a user-facing command —
> they are the **render substrate**, now implemented in code at
> `lib/comms_update.py` (`SlackAdapter`) and `lib/aurora_report.py::_render_slack`.
>
> For live Block Kit design reference (headers, sections, fields, context,
> dividers, status emoji, text progress bars, heartbeat/dashboard/alert
> templates), use the sibling **`styling-slack-blocks`** skill — it is *not*
> subsumed and remains the design-system reference.
>
> The full pattern catalogue that lived here is retained in git history
> (reversible, archive-never-delete).
