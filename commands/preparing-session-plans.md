---
description: "Load agent-skill skill source-command-preparing-session-plans: Load grip skill preparing-session-plans: Prepare executable plans for future GRIP sessions while current session has context. Use when current session has tokens remaining but task suits fresh context."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "agent-skill"
source-path: "${AGENTS_HOME:-$HOME/.agents}/skills/source-command-preparing-session-plans/SKILL.md"
---

# /preparing-session-plans

Load `source-command-preparing-session-plans` from `agent-skill`.

```bash
sed -n '1,220p' ${AGENTS_HOME:-$HOME/.agents}/skills/source-command-preparing-session-plans/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
