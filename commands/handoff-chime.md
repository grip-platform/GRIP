---
description: "Load grip skill handoff-chime: Pure-stdlib WAV notification chime generator for GRIP and the claude-handoff OSS plugin. Two-tone bell (880 Hz + 1320 Hz, perfect fifth) with ADSR envelope, 16-bit PCM mono, 44.1 kHz, ~0.4 s."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/handoff-chime/SKILL.md"
---

# /handoff-chime

Load `handoff-chime` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/handoff-chime/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
