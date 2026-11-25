---
description: "Load grip skill surfacing-skills: Build a slash-command catalogue of GRIP and Codex skills. Scans skill directories, extracts frontmatter, generates catalogue.json and catalogue.md."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/surfacing-skills/SKILL.md"
---

# /surfacing-skills

Load `surfacing-skills` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/surfacing-skills/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
