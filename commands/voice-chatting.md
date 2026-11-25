---
description: "Load grip skill voice-chatting: Activates GRIP's real-time voice chat mode. Every GRIP response is automatically spoken aloud through grip-speak.py using Gemini 2.5 Flash TTS, routed via the HAL council (non-OpenAI, zero marginal cost on the free\u2026"
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/voice-chatting/SKILL.md"
---

# /voice-chatting

Load `voice-chatting` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/voice-chatting/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
