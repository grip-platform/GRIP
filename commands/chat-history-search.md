---
description: "Load grip skill chat-history-search: Search and reference past conversations, plans, and insights from previous Claude Code sessions. Use when user references past chats, previous sessions, or invokes /remind-yourself."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/chat-history-search/SKILL.md"
---

# /chat-history-search

Load `chat-history-search` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/chat-history-search/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
