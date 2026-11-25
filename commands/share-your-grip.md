---
description: "Load grip skill share-your-grip: Guided onboarding wizard for the GRIP mesh service. Walks through pre-flight checks, starting grip-server, configuring consent, optional tunnel exposure, adding peers, and verifying the mesh."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/share-your-grip/SKILL.md"
---

# /share-your-grip

Load `share-your-grip` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/share-your-grip/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
