---
description: "Load grip skill voice-ai: Launch and manage the hands-free chief-of-staff Voice AI \u2014 Open-LLM-VTuber (mic \u2192 local sherpa-onnx ASR \u2192 Claude through the HAL Gateway :4010 \u2192 edge_tts speech + Live2D avatar) at localhost:12393."
argument-hint: "Optional task context"
generated-by: "surfacing-skills"
source: "grip"
source-path: "${CLAUDE_DIR:-$HOME/.claude}/skills/voice-ai/SKILL.md"
---

# /voice-ai

Load `voice-ai` from `grip`.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/voice-ai/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
