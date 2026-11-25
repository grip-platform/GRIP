---
description: "Load grip skill context-forensics: Explain where a session's context window actually went \u2014 decompose usage into ambient preload, rich media, tool-output bulk, harness injections, loop turns, and the accumulated-dialogue residual; deliver a one-paragraph\u2026"
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/context-forensics/SKILL.md"
---

# /context-forensics

Load `context-forensics` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/context-forensics/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
