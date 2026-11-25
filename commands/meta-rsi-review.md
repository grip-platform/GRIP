---
description: "Load grip skill meta-rsi-review: Self-review of GRIP PR reviews and suggestions. Enables recursive reasoning about own suggestions to close the RSI loop. Use when /review-my-review invoked or after PR review."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/meta-rsi-review/SKILL.md"
---

# /meta-rsi-review

Load `meta-rsi-review` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/meta-rsi-review/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
