---
description: "Load grip skill prune-tasks: Archive completed harness tasks to cap the SessionStart reminder injection. Captures every task body to state/tasks/archive.jsonl first, then deletes only the completed ones via TaskUpdate(status=\"deleted\")."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/prune-tasks/SKILL.md"
---

# /prune-tasks

Load `prune-tasks` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/prune-tasks/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
