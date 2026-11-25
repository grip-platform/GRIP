---
name: simple
description: "The simplest faithful explanation of a passage or a concept, as /clarify simple. One plain line first, then the idea for a bright beginner, with every fact, number, path and name kept exactly as written."
argument-hint: "the passage or the concept; nothing means the last answer"
---

# /simple

This is `/clarify simple`. Load the protocol, and follow its `simple` preset for `$ARGUMENTS`:

```bash
sed -n '1,220p' "${CLAUDE_DIR:-$HOME/.claude}/skills/clarify/SKILL.md"
```

Before you show the result, run the two checks that `commands/clarify.md` shows. Add `--keep-all` to the check against the passage, because this preset leaves nothing out.
