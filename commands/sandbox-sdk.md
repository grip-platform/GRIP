---
description: "Load codex skill sandbox-sdk: Build sandboxed applications for secure code execution. Load when building AI code execution, code interpreters, CI/CD systems, interactive dev environments, or executing untrusted code."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "codex"
source-path: "${CODEX_HOME:-$HOME/.codex}/skills/sandbox-sdk/SKILL.md"
---

# /sandbox-sdk

Load `sandbox-sdk` from `codex`.

```bash
sed -n '1,220p' ${CODEX_HOME:-$HOME/.codex}/skills/sandbox-sdk/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
