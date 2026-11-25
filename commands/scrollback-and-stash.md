---
description: "Load grip skill scrollback-and-stash: Clear terminal scrollback (cosmetic) + snapshot current session JSONL into compressed indexed stash. Future context-fetches route through 6-tier LRU instead of full JSONL reload."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/scrollback-and-stash/SKILL.md"
---

# /scrollback-and-stash

Load `scrollback-and-stash` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/scrollback-and-stash/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
