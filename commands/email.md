---
description: "Load grip skill email: Send and receive email via AgentMail. Pre-built inbox `you@agentmail.to`. Use for outbound notifications, onboarding emails, scheduled drafts, reading replies. Backed by the syscall-doctrine shim `lib/agentmail.py`."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/email/SKILL.md"
---

# /email

Load `email` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/email/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
