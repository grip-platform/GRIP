---
description: "Load agent-skill skill source-command-generating-e2e-tests: Load grip skill generating-e2e-tests: Automated E2E test infrastructure setup with Playwright, Vitest, MSW, and GitHub Actions. Use when user mentions add E2E tests, setup testing, or invokes /generate-e2e-tests."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "agent-skill"
source-path: "${AGENTS_HOME:-$HOME/.agents}/skills/source-command-generating-e2e-tests/SKILL.md"
---

# /generating-e2e-tests

Load `source-command-generating-e2e-tests` from `agent-skill`.

```bash
sed -n '1,220p' ${AGENTS_HOME:-$HOME/.agents}/skills/source-command-generating-e2e-tests/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
