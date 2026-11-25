---
description: "Load grip skill dfi-fib: DFI-FIB = Direct-First Improvement with Fibonacci depth control. The recursive self-improvement kernel at GRIP's converge core. See canonical references below."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/dfi-fib/SKILL.md"
---

# /dfi-fib

Load `dfi-fib` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/dfi-fib/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
