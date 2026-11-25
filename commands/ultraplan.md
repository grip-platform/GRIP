---
description: "Load grip skill ultraplan: Context-aware planning in a cloud session. Builds a self-contained prompt from this session's context and the task, then launches it with claude --cloud (the default) or as a one-time RemoteTrigger routine."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/ultraplan/SKILL.md"
---

# /ultraplan

Load `ultraplan` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/ultraplan/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
