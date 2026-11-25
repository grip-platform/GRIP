---
name: twr
description: Pull a TWR "Copy as context" tower from the macOS clipboard into the GRIP session. Parses TWR's markdown block (a "# Context:" header, "---"-separated clips, code fences) into structured clips GRIP uses as working context. Use when the operator clicks "Copy as context" in TWR, invokes /twr, or says "pull my tower", "ingest the TWR context", "load my clips", "twr clipboard".
status: active
version: 1.0.0
---

# twr — TWR tower into GRIP context

Bridges the TWR clipboard manager (CodeTonight-SA/twr) to a GRIP session. In
TWR, "Copy as context" on a tower writes one markdown block to the system
clipboard. `/twr` reads that block and turns it into structured context for the
current session — preserving stack order, provenance, and per-clip code fences.

No change to TWR is required: the integration surface is the system clipboard,
which GRIP already speaks.

## When to use

- Right after clicking **Copy as context** on a tower in TWR.
- The operator invokes `/twr`, or says "pull my tower", "ingest the TWR
  context", "load my clips", or "twr clipboard".

## What it does

1. Reads the clipboard via `pbpaste` (or stdin with `--source -`).
2. Detects TWR's format — a block starting with `# Context: <tower>`.
3. Parses it into `{tower, count, clips[]}`, clips oldest-first, code fences
   kept verbatim.
4. Surfaces the clips so the GRIP session continues with that tower as context.

If the clipboard does not hold a TWR context block, it says so and exits 1 — it
never guesses.

## Invocation

```bash
# Human summary of the tower currently on the clipboard:
PYTHONPATH=$HOME/.claude python3 $HOME/.claude/lib/twr_context.py

# Structured JSON (for piping into other GRIP steps):
PYTHONPATH=$HOME/.claude python3 $HOME/.claude/lib/twr_context.py --json

# Parse a payload from stdin (cross-platform / testing):
pbpaste | PYTHONPATH=$HOME/.claude python3 $HOME/.claude/lib/twr_context.py --source -
```

The GRIP session runs the `--json` form, reads the parsed clips, and continues
the conversation with that tower as working context.

## Format reference (from TWR source)

`formatTowerAsContext` (`electron/main/core/context-formatter.ts`) emits:

```
# Context: <tower name>

_<N> items from "<tower name>", oldest first._

<clip 1>

---

<clip 2>
```

Clips that are confidently code (parseable JSON, a shebang, `import … from`,
SQL ending in `;`, matched HTML tags) are wrapped in a fenced block with a
detected language. An empty tower emits `# Context: <name>` then `_(empty)_`.

## Example

````text
$ pbpaste | python3 lib/twr_context.py --source -
TWR tower "AGENT TEST" — 2 clip(s):

[1]
https://login.tailscale.com/a/1c82666f386acd

[2]
```json
{"plan": "max"}
```
````

## Limitations

- The clipboard path is macOS-only (`pbpaste`); use `--source -` elsewhere.
- A clip whose own text contains the exact sequence newline-newline-`---`-
  newline-newline would be over-split — this mirrors TWR's own delimiter
  choice, not a separate defect.
- v1 surfaces a tower to the live session only. Persisting a tower into KONO
  long-term memory (a future `--absorb` via `lib/hal/knowledge_absorption.py`)
  is intentionally out of scope for v1, not a stub.

## Backend & tests

- Backend: `lib/twr_context.py` — pure-stdlib parser + pbpaste/stdin CLI.
- Tests: `tests/test_twr_context.py` — fixtures mirror TWR's exact
  `context-formatter.ts` output (multi-clip, singular, empty, code-fenced,
  inline-`---`, non-TWR); Goodhart-resistant (fail under a wrong delimiter or a
  mis-stripped header).

## Related skills

| Skill | Relationship |
|-------|--------------|
| copy-cc-clean | Cleans clipboard artefacts; `/twr` parses a TWR-formatted clipboard |
| absorb-knowledge | Could persist parsed clips into KONO (future `--absorb`) |
| backlog | General staging inbox; `/twr` is the TWR-specific fast path |
