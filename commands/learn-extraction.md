---
description: "Load grip skill learn-extraction: Extract patterns and learnings from completed work and save to semantic/episodic memory for future reuse. Assigns confidence levels (LOW/MEDIUM/HIGH) based on number of examples."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/learn-extraction/SKILL.md"
---

# /learn-extraction

Load `learn-extraction` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/learn-extraction/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
