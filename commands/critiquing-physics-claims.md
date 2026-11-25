---
description: "Load grip skill critiquing-physics-claims: Applies a Hossenfelder-style skeptical review to theoretical physics ideas. Checks testability, mathematical rigour, novelty, definition precision, scope honesty, and red flags, then assigns a 0-10 bullshit meter score\u2026"
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/critiquing-physics-claims/SKILL.md"
---

# /critiquing-physics-claims

Load `critiquing-physics-claims` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/critiquing-physics-claims/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
