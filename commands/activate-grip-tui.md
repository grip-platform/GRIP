---
description: "Load grip skill activate-grip-tui: TUI++ activation wizard with three-act structure (Identity \u2192 Capability \u2192 First Run). Use when /activate-grip invoked, fresh install detected (no .onboarded.json), or identity reset requested."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/activate-grip-tui/SKILL.md"
---

# /activate-grip-tui

Load `activate-grip-tui` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/activate-grip-tui/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
