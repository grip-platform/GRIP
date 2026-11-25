---
description: "Load grip skill task-reconcile: Reconcile harness tasks against reality before trusting their status. Extracts machine-checkable referents (URL/PR/path/repo) from each open task's text, probes them, and classifies each task PROVABLY-DONE /\u2026"
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/task-reconcile/SKILL.md"
---

# /task-reconcile

Load `task-reconcile` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/task-reconcile/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
