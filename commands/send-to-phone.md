---
description: "Load grip skill send-to-phone: Universal phone delivery command. Routes any content to the operator's personal device via the best available transport (WhatsApp \u2192 Slack DM \u2192 iMessage cascade). Wraps /send-whatsapp as the primary path."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/send-to-phone/SKILL.md"
---

# /send-to-phone

Load `send-to-phone` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/send-to-phone/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
