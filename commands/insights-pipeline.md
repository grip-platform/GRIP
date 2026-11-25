---
description: "Load grip skill insights-pipeline: Run the insights-driven learning pipeline - detect patterns, generate skill proposals, extract red-team TODOs, and create GitHub issues. Use when running RSI loops or periodic quality checks."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/insights-pipeline/SKILL.md"
---

# /insights-pipeline

Load `insights-pipeline` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/insights-pipeline/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
