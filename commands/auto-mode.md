---
description: "Load grip skill auto-mode: LLM-inferred mode and skill selection. Uses free external models (Gemini Flash, Groq) to semantically analyse prompts and activate optimal mode stacks. Replaces keyword matching with genuine inference."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/auto-mode/SKILL.md"
---

# /auto-mode

Load `auto-mode` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/auto-mode/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
