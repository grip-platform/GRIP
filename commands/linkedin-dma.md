---
description: "Load grip skill linkedin-dma: Access LinkedIn Member Data Portability (DMA) APIs to fetch the authenticated member's own data \u2014 profile, posts, articles, and recent changelog events. Geographically restricted; see the skill for access requirements."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/linkedin-dma/SKILL.md"
---

# /linkedin-dma

Load `linkedin-dma` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/linkedin-dma/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
