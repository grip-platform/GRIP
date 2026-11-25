---
description: "Load agent-skill skill source-command-searching-chat-history: Load grip skill searching-chat-history: Search and reference past conversations, plans, and insights from previous Codex sessions. Use when user references past chats, previous sessions, or invokes /remind-yourself."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "agent-skill"
source-path: "${AGENTS_HOME:-$HOME/.agents}/skills/source-command-searching-chat-history/SKILL.md"
---

# /searching-chat-history

Load `source-command-searching-chat-history` from `agent-skill`.

```bash
sed -n '1,220p' ${AGENTS_HOME:-$HOME/.agents}/skills/source-command-searching-chat-history/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
