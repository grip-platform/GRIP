---
description: "Load grip skill sensitive-repo-bootstrap: Bootstrap a private GitHub repo where every sensitive file is git-crypt encrypted at rest, so files appear as ciphertext on GitHub and the symmetric key never leaves the machine."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/sensitive-repo-bootstrap/SKILL.md"
---

# /sensitive-repo-bootstrap

Load `sensitive-repo-bootstrap` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/sensitive-repo-bootstrap/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
