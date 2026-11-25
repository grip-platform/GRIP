---
description: "Load agent-skill skill source-command-legal-mode-orchestration: Load grip skill legal-mode-orchestration: Orchestrates legal-ops dual-track workflows with jurisdiction awareness and quality gates. Use when /mode legal is active and legal document work begins."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "agent-skill"
source-path: "${AGENTS_HOME:-$HOME/.agents}/skills/source-command-legal-mode-orchestration/SKILL.md"
---

# /legal-mode-orchestration

Load `source-command-legal-mode-orchestration` from `agent-skill`.

```bash
sed -n '1,220p' ${AGENTS_HOME:-$HOME/.agents}/skills/source-command-legal-mode-orchestration/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
