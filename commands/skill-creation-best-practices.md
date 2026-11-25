---
description: "Load grip skill skill-creation-best-practices: PARAMOUNT quality gate for ALL skill creation. Enforces Anthropic best practices and GRIP standards before any skill is created, whether proposed, auto-generated, or autonomously discovered."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/skill-creation-best-practices/SKILL.md"
---

# /skill-creation-best-practices

Load `skill-creation-best-practices` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/skill-creation-best-practices/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
