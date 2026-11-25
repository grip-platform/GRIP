---
description: "Load grip skill otty: Otty terminal integration \u2014 status card, doctor, and otty-pad (write prompts in a real editor pane, ship them into any Otty pane running any harness \u2014 Claude Code, Codex, OpenCode)."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/otty/SKILL.md"
---

# /otty

Load `otty` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/otty/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
