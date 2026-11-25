---
description: "Load grip skill hal-provider-refresh: Refresh the HAL provider catalog \u2014 research current models across providers, adversarially verify in two passes so no hallucinated model enters the catalog, and diff against config/providers.yaml and\u2026"
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/hal-provider-refresh/SKILL.md"
---

# /hal-provider-refresh

Load `hal-provider-refresh` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/hal-provider-refresh/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
