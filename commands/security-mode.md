---
description: "Load grip skill security-mode: Routes security auditing through STRIDE, OWASP, and DREAD frameworks. Use when /mode security is active."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/security-mode/SKILL.md"
---

# /security-mode

Load `security-mode` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/security-mode/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
