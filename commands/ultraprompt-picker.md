---
description: "Load grip skill ultraprompt-picker: On a fresh SessionStart, surface on-disk continuation briefs (ultraprompts) that haven't been executed and let the operator pick which to run via AskUserQuestion."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/ultraprompt-picker/SKILL.md"
---

# /ultraprompt-picker

Load `ultraprompt-picker` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/ultraprompt-picker/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
