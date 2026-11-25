---
name: clarify
description: Make a passage clear for the person who reads it or acts on it, and keep its receipts. One command for /tldr, /plain-language, /eli5 and /encapsulate (now its presets), plus essence for provenance and simple (/simple) for the simplest faithful explanation. Writes in 80% ASD-STE100, checked by lib/ste_lint.py.
argument-hint: "[line|act|plain|novice|essence|simple] [--diagram|--html] the passage; nothing means the last answer"
---

# /clarify

Load the protocol, then follow it for `$ARGUMENTS`:

```bash
sed -n '1,220p' "${CLAUDE_DIR:-$HOME/.claude}/skills/clarify/SKILL.md"
```

Before you show the result, pass the draft to the linter on standard input, and fix every error. Put the draft between the two `DRAFT` lines, unchanged:

```bash
"${CLAUDE_DIR:-$HOME/.claude}/bin/grip-py" "${CLAUDE_DIR:-$HOME/.claude}/lib/ste_lint.py" <<'DRAFT'
<the draft, verbatim>
DRAFT
```

Exit 0 means no errors. Exit 1 lists each error with its line number. The quotes around `'DRAFT'` stop the shell from changing any text in the draft. A draft that is already in a file can be linted by its path instead.

When you rewrote a passage, also check the output against that passage. Put each text between its two marker lines, unchanged:

```bash
d=$(mktemp -d)
cat > "$d/passage.md" <<'PASSAGE'
<the passage, verbatim>
PASSAGE
cat > "$d/output.md" <<'OUTPUT'
<the output, verbatim>
OUTPUT
"${CLAUDE_DIR:-$HOME/.claude}/bin/grip-py" "${CLAUDE_DIR:-$HOME/.claude}/lib/clarify_check.py" "$d/passage.md" "$d/output.md"
```

An error means that the output has a token the passage does not have: a number, command, path, issue id or URL. An OBSERVED anchor that is not on disk is also an error. Remove the token, or show where it came from. A warning lists the tokens that you dropped. Name each one that you left out on purpose on a line that starts "Left out:".

For the `simple` preset, add `--keep-all` at the end of the `clarify_check.py` command. A dropped token is then an error, and a "Left out:" line does not excuse it.
