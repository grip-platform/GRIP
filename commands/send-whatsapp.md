---
description: "Load grip skill send-whatsapp: Send any content to a WhatsApp recipient via grip-channel. Accepts text, files, structured data, notes, function output \u2014 GRIP infers the delivery type."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/send-whatsapp/SKILL.md"
---

# /send-whatsapp

Load `send-whatsapp` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/send-whatsapp/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
