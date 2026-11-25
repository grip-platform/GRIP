---
description: "Load grip skill precog-idr: Precognitive orchestration-head IDR \u2014 emit a happi/1.1 signed envelope capturing the dispatch decision (effort tier, provider, fingerprint, predecessor chain) BEFORE delegating to HAL/downstream providers."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/precog-idr/SKILL.md"
---

# /precog-idr

Load `precog-idr` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/precog-idr/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
