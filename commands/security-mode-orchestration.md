---
description: "Load agent-skill skill source-command-security-mode-orchestration: Load grip skill security-mode-orchestration: Routes security auditing through STRIDE, OWASP, and DREAD frameworks. Use when /mode security is active."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "agent-skill"
source-path: "${AGENTS_HOME:-$HOME/.agents}/skills/source-command-security-mode-orchestration/SKILL.md"
---

# /security-mode-orchestration

Load `source-command-security-mode-orchestration` from `agent-skill`.

```bash
sed -n '1,220p' ${AGENTS_HOME:-$HOME/.agents}/skills/source-command-security-mode-orchestration/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
