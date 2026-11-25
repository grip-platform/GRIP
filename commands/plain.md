---
description: "This is /plain-language: the same text, with each insider term swapped for its plain word, in 80% ASD-STE100."
argument-hint: "the passage; or metric|gate|session|term and its argument"
---

# /plain

This is `/plain-language`. Load the protocol and follow it for `$ARGUMENTS`:

```bash
sed -n '1,220p' "${CLAUDE_DIR:-$HOME/.claude}/skills/plain-language/SKILL.md"
```
