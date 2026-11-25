---
description: "Load grip skill riper-workflow: Structured five-phase analysis \u2014 Research, Innovate, Plan, Execute, Review \u2014 with permission levels per phase. Use when a task needs disciplined phase separation rather than jumping straight to code."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/riper-workflow/SKILL.md"
---

# /riper-workflow

Load `riper-workflow` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/riper-workflow/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
