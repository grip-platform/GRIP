---
description: "Load codex skill durable-objects: Create and review Cloudflare Durable Objects. Use when building stateful coordination (chat rooms, multiplayer games, booking systems), implementing RPC methods, SQLite storage, alarms, WebSockets, or reviewing DO code\u2026"
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "codex"
source-path: "${CODEX_HOME:-$HOME/.codex}/skills/durable-objects/SKILL.md"
---

# /durable-objects

Load `durable-objects` from `codex`.

```bash
sed -n '1,220p' ${CODEX_HOME:-$HOME/.codex}/skills/durable-objects/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
