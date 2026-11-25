---
description: "Load grip skill mesh: Three-layer Claude Code session mesh \u2014 local WebSocket bus, Tailscale cross-machine relay, and presence/locking. Enables real-time inter-session messaging and pair programming across collaborator machines."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/mesh/SKILL.md"
---

# /mesh

Load `mesh` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/mesh/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
