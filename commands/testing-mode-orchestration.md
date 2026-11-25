---
description: "Load agent-skill skill testing-mode-orchestration: Routes QA through human-in-the-loop gates with Devil's Advocate framework. Use when /mode testing is active."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "agent-skill"
source-path: "${AGENTS_HOME:-$HOME/.agents}/skills/.archive/testing-mode/SKILL.md"
---

# /testing-mode-orchestration

Load `testing-mode-orchestration` from `agent-skill`.

```bash
sed -n '1,220p' ${AGENTS_HOME:-$HOME/.agents}/skills/.archive/testing-mode/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
