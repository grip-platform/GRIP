---
description: "Load grip skill handoff-prompt: Generate a MINIMAL next-session continuation prompt (the specific next action + task/artifact pointers, <=400 chars) and auto-copy it to the system clipboard, so the operator pastes ONE line into the new session."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/handoff-prompt/SKILL.md"
---

# /handoff-prompt

Load `handoff-prompt` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/handoff-prompt/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
