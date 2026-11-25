---
description: "Load grip skill python-compat-guard: Prevent Python version compatibility issues in cross-environment codebases."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/python-compat-guard/SKILL.md"
---

# /python-compat-guard

Load `python-compat-guard` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/python-compat-guard/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
