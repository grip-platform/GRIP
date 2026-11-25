---
description: "Load grip skill eli5: Rewrite any document, explanation, or article for a bright 5-year-old \u2014 short sentences, concrete analogies, no jargon, no acronyms, but same respect for the reader."
argument-hint: "Optional task context"
---

# /eli5

Load `eli5` from `grip`.

This is the `novice` preset of `/clarify` (`skills/clarify/SKILL.md`). Its shared rules apply here too: nothing added, words to type unchanged, 80% ASD-STE100, and one structural analogy at most.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/eli5/SKILL.md
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
