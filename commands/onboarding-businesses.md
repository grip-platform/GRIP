---
description: "Load grip skill onboarding-businesses: TUI wizard for voice assistant configuration using articulate-first pattern. Use when user invokes /onboard-business or wants to set up a new business voice assistant."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/onboarding-businesses/SKILL.md"
---

# /onboarding-businesses

Load `onboarding-businesses` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/onboarding-businesses/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
