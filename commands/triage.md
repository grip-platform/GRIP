---
description: "Load grip skill triage: Safely triage lingering feature branches \u2014 classify each as already-on-main, fast-forward, needs-PR, or diverged, then act."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/triage/SKILL.md"
---

# /triage

Load `triage` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/triage/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
