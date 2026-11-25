---
description: "Load grip skill heartbeat: GRIP's autonomous learning pulse. Manage the periodic /evo + /learn pipeline that runs without human invocation."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/heartbeat/SKILL.md"
---

# /heartbeat

Load `heartbeat` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/heartbeat/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
