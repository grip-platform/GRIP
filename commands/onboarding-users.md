---
description: "Load grip skill onboarding-users: Legacy onboarding skill. Now redirects to /activate-grip for unified authentication and identity setup. Maintained for backward compatibility."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/onboarding-users/SKILL.md"
---

# /onboarding-users

Load `onboarding-users` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/onboarding-users/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
