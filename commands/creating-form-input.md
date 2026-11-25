---
description: "Load grip skill creating-form-input: Documents Claude Code's form-input schema for AskUserQuestion \u2014 the typed question kinds choice, text and number \u2014 and the house contract that keeps every option explained after the schema stops requiring it."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/creating-form-input/SKILL.md"
---

# /creating-form-input

Load `creating-form-input` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/creating-form-input/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
