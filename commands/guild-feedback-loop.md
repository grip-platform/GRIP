---
description: "Load agent-skill skill guild-feedback-loop: Human-in-the-loop calibration loop for GRIP quality gates. Post content to Guild, collect human ratings, compare against machine judge, RCA disagreements, fix root causes, re-test."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "agent-skill"
source-path: "${AGENTS_HOME:-$HOME/.agents}/skills/guild-feedback-loop/SKILL.md"
---

# /guild-feedback-loop

Load `guild-feedback-loop` from `agent-skill`.

```bash
sed -n '1,220p' ${AGENTS_HOME:-$HOME/.agents}/skills/guild-feedback-loop/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
