---
name: simple
description: "Turns a passage or a concept into its simplest faithful explanation: one plain line first, then the idea for a bright beginner, in 80% ASD-STE100. Keeps every fact, number, path and name exactly as written, and keeps each marked uncertainty. Use when the operator asks to make something simple or to explain it simply, or invokes /simple. It is the simple preset of /clarify."
user_invocable: true
command: simple
status: Active
version: 1.0.0
created: 2026-11-22
triggers:
  - /simple
  - make this simple
  - explain it simply
  - simplify this
integrates:
  - clarify
---

# /simple: the simplest faithful explanation

## What it does

`/simple` turns a passage or a concept into its simplest faithful explanation. The first line gives the one fact that the reader needs. The text after it explains the idea for a bright beginner, who follows it in one pass. Simple never means wrong or vague. Every fact, number, path and name stays exactly as written, and each marked uncertainty stays marked.

## How to run it

`/simple` is the `simple` preset of `/clarify`, so `/simple` and `/clarify simple` are the same command. The rules, the checks and a worked example are in `skills/clarify/SKILL.md`, and this file only routes to them. Load that protocol, and follow its `simple` preset:

```bash
sed -n '1,220p' "${CLAUDE_DIR:-$HOME/.claude}/skills/clarify/SKILL.md"
```

Before you show the result, run the two checks that `commands/clarify.md` shows. Add `--keep-all` to the check against the passage, so that each dropped token is an error.

## Examples

```text
/simple [passage]       # the same as /clarify simple [passage]
/simple a Merkle tree   # a concept with no passage
/simple                 # the last answer in this conversation, made simple
```

## How it differs from its neighbours

| Command | Gives |
|---|---|
| `/tldr` (`line`) | only the one fact, in 25 words at most |
| `/plain` (`plain`) | the same text, with each insider term in plain words |
| `/eli5` (`novice`) | the idea for a bright beginner, with one analogy |
| `/simple` (`simple`) | the one fact first, then the idea for a bright beginner, with every fact, number and name kept |

The check behind the last row is the "Every fact kept" row of the invariants table in `skills/clarify/SKILL.md`.

## Where it came from

V>> asked for it on 2026-11-22, in these words: "/create the `/simple` skill -- STE, /plain-language /eli5 /tldr /clarify style". It applies the canonical voice of `rules/canonical-output-style.md` to any passage on demand: plain and short, with an `/eli5` explanation for an abstract idea. Claude Opus 5.5 (`claude-opus-5-5`) wrote the preset, this file and their tests.

## Falsification

Over the next 20 uses, `/simple` is wrong if a reader finds more than one fact that its output changed, rounded or dropped. It is also wrong if, for the same kind of passage, the operator chooses `/clarify novice` or `/eli5` over it in most uses. Then fold the preset back into `novice`. That is cheap, because the preset is one row and one section of `skills/clarify/SKILL.md`. The `--keep-all` check is wrong if it passes an output that a reader shows to drop a number, path or id of the passage. Track the facts that readers find changed or dropped, with a target of 0.
