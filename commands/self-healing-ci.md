---
description: "Load grip skill self-healing-ci: Installs a self-healing nightly CI system into any GitHub project. Nightly full-suite runs detect regressions; each regression triggers an inner converge loop that must produce a fix plus a regression-anchor test\u2026"
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/self-healing-ci/SKILL.md"
---

# /self-healing-ci

Load `self-healing-ci` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/self-healing-ci/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
