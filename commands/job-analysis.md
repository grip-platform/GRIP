---
description: "Load agent-skill skill job-analysis: Structured role fit analysis against M>>'s profile. Produces a scored breakdown across domain alignment, culture fit, growth trajectory, and red flags. Feeds role-pattern-matching ability."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "agent-skill"
source-path: "${AGENTS_HOME:-$HOME/.agents}/skills/job-analysis/SKILL.md"
---

# /job-analysis

Load `job-analysis` from `agent-skill`.

```bash
sed -n '1,220p' ${AGENTS_HOME:-$HOME/.agents}/skills/job-analysis/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
