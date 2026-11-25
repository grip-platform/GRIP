---
description: "Load grip skill llm-usage: One-glance dashboard of GRIP's REAL multi-provider LLM usage (groq/gemini/qwen/deepseek/xai/anthropic via HAL), with IDR-style /verify provenance \u2014 a tamper-evident signed chain proving the per-provider counts."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/llm-usage/SKILL.md"
---

# /llm-usage

Load `llm-usage` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/llm-usage/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
