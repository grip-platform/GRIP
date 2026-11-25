---
description: "Load grip skill creating-wizards: Meta-skill for building multi-step AskUserQuestion wizard flows. Use when designing onboarding, configuration, or any multi-question user interaction. Codifies anti-patterns, branching logic, and progressive disclosure."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/creating-wizards/SKILL.md"
---

# /creating-wizards

Load `creating-wizards` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/creating-wizards/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
