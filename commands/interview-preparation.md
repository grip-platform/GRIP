---
description: "Load agent-skill skill interview-preparation: Role-specific interview preparation including company research briefing, STAR story bank from M>>'s CV, technical prep checklist, and mock Q&A with evaluation rubric."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "agent-skill"
source-path: "${AGENTS_HOME:-$HOME/.agents}/skills/interview-preparation/SKILL.md"
---

# /interview-preparation

Load `interview-preparation` from `agent-skill`.

```bash
sed -n '1,220p' ${AGENTS_HOME:-$HOME/.agents}/skills/interview-preparation/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
