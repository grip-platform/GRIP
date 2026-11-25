---
description: "Load grip skill redact: Canonical redaction / PII shield \u2014 redact client names, personal identities, emails, phones, IPs, JIDs, hosts and key shapes from ANY text before it reaches a public surface or a cloud LLM, with pinned pseudonyms so the\u2026"
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/redact/SKILL.md"
---

# /redact

Load `redact` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/redact/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
