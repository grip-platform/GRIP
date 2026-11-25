---
name: copy-cc-clean
description: Strips leading whitespace and Claude Code quote-block artefacts (▎ ▌ ▏ │) from every line in the system clipboard and copies the cleaned result back. Use when Claude Code output contains unwanted indentation, padding, or rendering artefacts from code blocks, markdown formatting, or terminal display.
status: Active
version: 1.1.0
triggers:
  - /copy-cc-clean
  - "strip clipboard whitespace"
  - "clean clipboard indentation"
  - "strip leading whitespace from clipboard"
  - "strip cc artefacts"
---

# copy-cc-clean

Strips leading whitespace AND Claude Code quote-block rendering artefacts from every line in the system clipboard, then copies the cleaned text back — ready to paste.

## What It Does

Two-stage clean of the current clipboard, applied in order:

1. **Stage 1** — Strip all leading whitespace (spaces and tabs) from the start of each line.
2. **Stage 2** — Strip leading Claude Code quote-block artefact characters, optionally followed by a single space.

Reports the line count on completion.

Note: stage 1 strips ALL leading whitespace per line. Relative indentation between lines (inner structure) is preserved only if all lines share the same leading offset — if they differ, each is stripped to the left margin independently. For common-prefix-only dedent, use Python's `textwrap.dedent` instead. Stage 2 only touches the FIRST character of each line — mid-line `▎` characters are never modified.

## Claude Code Artefact Characters

These leading-only prefixes are stripped after whitespace removal:

| Char | Unicode | Description | Where it appears |
|------|---------|-------------|-------------------|
| `▎` | U+258E | LEFT VERTICAL ONE QUARTER BLOCK | Claude Code quote-block highlight |
| `▌` | U+258C | LEFT HALF BLOCK | Claude Code diff/highlight emphasis |
| `▏` | U+258F | LEFT ONE EIGHTH BLOCK | Claude Code narrow quote variant |
| `│` | U+2502 | BOX DRAWINGS LIGHT VERTICAL | Tree/quote rendering line |

Each char is stripped ONLY when it is the FIRST character of a line (after stage 1's whitespace strip), optionally followed by a single space. Mid-line occurrences are preserved.

## Command

Execute this Bash command directly — no confirmation needed:

```bash
pbpaste > /tmp/cc_clean.txt \
  && sed 's/^[[:space:]]*//' /tmp/cc_clean.txt | sed 's/^[▎▌▏│][[:space:]]*//' | pbcopy \
  && echo "Cleaned. Lines: $(pbpaste | wc -l | tr -d ' ')" \
  && trash /tmp/cc_clean.txt
```

Always use `trash` (not `rm`) to delete the temp file — GRIP guardrail.

## Platform Variants

| Platform | Read clipboard | Write clipboard | Delete temp |
|----------|---------------|-----------------|-------------|
| macOS | `pbpaste` | `pbcopy` | `trash` |
| Linux | `xclip -o` | `xclip -selection clipboard` | `gio trash` |
| Windows (Git Bash) | `powershell Get-Clipboard` | `powershell Set-Clipboard` | `rm` |

macOS is the primary target. Adapt the pipeline for other platforms using the variants above. The two-stage sed pipeline works on BSD sed (macOS default) and GNU sed (Linux) — verified for multibyte char handling with default UTF-8 locale.

## Examples

### Example 1 — Pure leading whitespace (legacy behaviour)

**Input clipboard** — Claude Code draft with leading padding:

```text
    Hey TFC

    Something we quietly shipped just went public.

    *HAPPI/1.0* — a protocol...
```

**Output clipboard** — after `/copy-cc-clean`:

```text
Hey TFC

Something we quietly shipped just went public.

*HAPPI/1.0* — a protocol...
```

### Example 2 — Quote-block artefact

**Input clipboard** — Claude Code reply with `▎` quote-block highlight:

```text
    ▎ Both answers land — but I'd lean Y.
    ▎ Reason: it isolates the dispatcher logic from the transport.
    ▎ If you go X, you'll re-hit the same problem in 2 sprints.
```

**Output clipboard** — after `/copy-cc-clean`:

```text
Both answers land — but I'd lean Y.
Reason: it isolates the dispatcher logic from the transport.
If you go X, you'll re-hit the same problem in 2 sprints.
```

### Example 3 — Mid-line artefact preserved

**Input clipboard** — content that contains `▎` mid-line:

```text
The status bar shows: foo ▎ bar ▎ baz
```

**Output clipboard** — after `/copy-cc-clean` (mid-line ▎ untouched):

```text
The status bar shows: foo ▎ bar ▎ baz
```

## Changelog

| Version | Date | Change |
|---------|------|--------|
| 1.1.0 | 2026-05-24 | Strip Claude Code quote-block artefacts (▎ ▌ ▏ │) after leading-whitespace strip |
| 1.0.0 | 2026-04-22 | Initial creation (Gen 0, happi-site session) |
