---
description: "Load grip skill copilot-delegate: Delegates cheap factual queries, explanations, command suggestions, and routine diff reviews to GitHub Copilot CLI (free GPT-4.1) instead of burning Premium Claude tokens."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/copilot-delegate/SKILL.md"
---

# /copilot-delegate

Load `copilot-delegate` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/copilot-delegate/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
