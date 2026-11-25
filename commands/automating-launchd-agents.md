---
description: "Load agent-skill skill source-command-automating-launchd-agents: Load grip skill automating-launchd-agents: Create self-reloading macOS launchd agents with automatic plist validation. Use when user needs scheduled tasks, cron jobs, or nightly automation on macOS."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "agent-skill"
source-path: "${AGENTS_HOME:-$HOME/.agents}/skills/source-command-automating-launchd-agents/SKILL.md"
---

# /automating-launchd-agents

Load `source-command-automating-launchd-agents` from `agent-skill`.

```bash
sed -n '1,220p' ${AGENTS_HOME:-$HOME/.agents}/skills/source-command-automating-launchd-agents/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
