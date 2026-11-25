---
description: "Load grip skill osa-phone-action: Inbound WhatsApp \u2192 macOS OSA action bridge. Decodes WhatsApp messages in the format \"/osa <script>\" or \"/mac <script>\" from authorised JIDs and dispatches to the HAL OSA registry."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/osa-phone-action/SKILL.md"
---

# /osa-phone-action

Load `osa-phone-action` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/osa-phone-action/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
