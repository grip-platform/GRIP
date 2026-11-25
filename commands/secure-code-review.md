---
description: "Load grip skill secure-code-review: Security-focused code review against OWASP Top 10 and CWE patterns. Use when reviewing code for vulnerabilities or running a pre-merge security assessment."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/secure-code-review/SKILL.md"
---

# /secure-code-review

Load `secure-code-review` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/secure-code-review/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
