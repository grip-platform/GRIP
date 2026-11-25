---
description: "Load grip skill pair-mode: Pair with GRIP: the operator drives and GRIP navigates by default, thinking aloud and checkpointing through the /hitl family, the roles swap on a word, and /mesh carries the pair across machines or sessions."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/pair-mode/SKILL.md"
---

# /pair-mode

Load `pair-mode` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/pair-mode/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
