---
description: "Load grip skill verify-queue: Verify-gated markdown task queue. Drop tasks into state/queue/pending/, runner moves through active/, deletes on verify pass or moves to failed/ on verify fail."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/verify-queue/SKILL.md"
---

# /verify-queue

Load `verify-queue` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/verify-queue/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
