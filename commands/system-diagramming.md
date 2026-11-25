---
description: "Load grip skill system-diagramming: Structured system visualisation with standard notation, and GRIP's tested Mermaid workflow from source to a checked picture. Use when documenting architecture, communicating a design, or creating, checking or repairing a Mermaid diagram."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/system-diagramming/SKILL.md"
---

# /system-diagramming

Load `system-diagramming` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/system-diagramming/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
