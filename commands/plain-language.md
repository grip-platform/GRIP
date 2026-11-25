---
description: "The STE entry point: /plain-language runs the plain preset of /clarify, in 80% ASD-STE100, linted by lib/ste_lint.py."
argument-hint: "the text to translate; or metric|gate|session|term and its argument"
---

# /plain-language

`/plain-language` is the STE entry point, and `/plain` is its short alias.
Load the protocol and follow its STE path for `$ARGUMENTS`:

```bash
sed -n '1,220p' "${CLAUDE_DIR:-$HOME/.claude}/skills/plain-language/SKILL.md"
```

For free text, run `/clarify plain` on the passage, then lint it exactly as
`commands/clarify.md` shows:

```bash
"${CLAUDE_DIR:-$HOME/.claude}/bin/grip-py" "${CLAUDE_DIR:-$HOME/.claude}/lib/ste_lint.py" <<'DRAFT'
<the draft, verbatim>
DRAFT
```

Fix every error before you show the rewrite. `metric`, `gate`, `session` and
`term` are unchanged -- see the skill for their `lib/plain_language.py` calls.
