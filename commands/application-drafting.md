---
description: "Load agent-skill skill application-drafting: CV tailoring and cover letter drafting for specific roles. Uses M>>'s profile from facts/cv.md and the job-search-voice ability to match her authentic writing style. Enforces anti-fluff gate."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "agent-skill"
source-path: "${AGENTS_HOME:-$HOME/.agents}/skills/application-drafting/SKILL.md"
---

# /application-drafting

Load `application-drafting` from `agent-skill`.

```bash
sed -n '1,220p' ${AGENTS_HOME:-$HOME/.agents}/skills/application-drafting/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
