---
description: "Load grip skill decision-lifecycle: Record, track, resolve, and audit decisions across their full lifecycle. Use when recording decisions, checking outcomes, running checkpoint reviews, or voiding stale entries."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/decision-lifecycle/SKILL.md"
---

# /decision-lifecycle

Load `decision-lifecycle` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/decision-lifecycle/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
