---
description: "Load agent-skill skill source-command-reverse-engineering-apis: Load grip skill reverse-engineering-apis: Systematically reverse engineer authenticated web APIs from browser DevTools captures. Use when user provides network request details or invokes /reverse-api."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "agent-skill"
source-path: "${AGENTS_HOME:-$HOME/.agents}/skills/source-command-reverse-engineering-apis/SKILL.md"
---

# /reverse-engineering-apis

Load `source-command-reverse-engineering-apis` from `agent-skill`.

```bash
sed -n '1,220p' ${AGENTS_HOME:-$HOME/.agents}/skills/source-command-reverse-engineering-apis/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
