---
description: "Load grip skill tunnel: Expose localhost to a public URL via cloudflared (primary) or localtunnel (fallback) with auto-caffeinate. Use when the user wants to share a local server, demo a running app, or create a temporary public URL for\u2026"
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/tunnel/SKILL.md"
---

# /tunnel

Load `tunnel` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/tunnel/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
