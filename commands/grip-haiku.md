---
description: "Load grip skill grip-haiku: Turns the live GRIP genome fitness snapshot into a 3-line haiku + one-line health verdict. No LLM, no network \u2014 deterministic, beautiful, instant."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/grip-haiku/SKILL.md"
---

# /grip-haiku

Load `grip-haiku` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/grip-haiku/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
