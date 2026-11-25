---
description: "Load agent-skill skill offer-evaluation: Structured analysis of job offers including total compensation breakdown, market benchmarking, and negotiation strategy. Uses salary-calibration ability. Links to decision-mode for final decision matrix."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "agent-skill"
source-path: "${AGENTS_HOME:-$HOME/.agents}/skills/offer-evaluation/SKILL.md"
---

# /offer-evaluation

Load `offer-evaluation` from `agent-skill`.

```bash
sed -n '1,220p' ${AGENTS_HOME:-$HOME/.agents}/skills/offer-evaluation/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
