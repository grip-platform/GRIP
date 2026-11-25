---
description: "Load grip skill server-mode: Always-on server posture for the second Mac as the WhatsApp/Slack relay exit-node. Gives each relay-spawned session warm per-channel context (channel history + warm-start kernel + KONO recall + cross-machine mesh) instead\u2026"
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/server-mode/SKILL.md"
---

# /server-mode

Load `server-mode` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/server-mode/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
