---
description: Rank the candidate next moves, derive how each should run, then ask once
argument-hint: "[k]  (slate size, default 3)"
allowed-tools: Bash, Read, AskUserQuestion
---

Follow `skills/nextmove/SKILL.md`.

Build the slate, and note that it reports its own scope:

```bash
PYTHONPATH="${CLAUDE_DIR:-$HOME/.claude}/lib" python3 -c \
  "import next_move as n; print(n.render(n.slate(k=${1:-3})))"
```

Then: if AFK or auto/RSI is engaged take rank 1 without asking; otherwise render
the slate with its scope line, validate the questions through
`hitl_quality_gates.collect_violations`, and ask once — action and manner in a
single batched call.
