---
description: "Load grip skill ability-framework: TOMBSTONE \u2014 the ABILITY concept (a persistent, self-improving knowledge substrate, distinct from a static SKILL) is preserved here for cheap re-derivation, but the runtime was never built (N=0). Not active."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/ability-framework/SKILL.md"
---

# /ability-framework

Load `ability-framework` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/ability-framework/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
