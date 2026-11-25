---
description: "Load grip skill absorb-knowledge: Absorb a single piece of external text (Slack message, Discord post, quote) into GRIP's MemoryEngine via the canonical syscall at lib/hal/knowledge_absorption.py."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/absorb-knowledge/SKILL.md"
---

# /absorb-knowledge

Load `absorb-knowledge` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/absorb-knowledge/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
