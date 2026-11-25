---
description: "Load grip skill evidence-evaluation: Systematic evidence-quality assessment using the CRAAP framework. Use when evaluating claims from external sources, judging research quality, or deciding how much weight a source deserves."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/evidence-evaluation/SKILL.md"
---

# /evidence-evaluation

Load `evidence-evaluation` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/evidence-evaluation/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
