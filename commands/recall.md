---
description: The one recall command (Rule 0, Recall First). With no question it resumes the session from every memory subsystem; with a question it runs the layered local cascade and states what it searched.
disable-model-invocation: false
---

# /recall

The one recall command. The full protocol is `skills/recall/SKILL.md`.

## Usage

```bash
/recall                          # resume: every memory subsystem
/recall --quiet                  # resume as a single summary line
/recall "<question>"             # ask: the layered cascade
/recall --not "<claim>"          # absence: proven, or the scopes not searched
/recall --why "<subject>"        # follow the links from the matching records
/recall --deep "<question>"      # allow the escalation layers
```

## Implementation

Load and execute the recall skill:

```
Skill(skill="recall")
```

The resume runs `~/.grip/venv/bin/python ~/.claude/lib/grip_recall.py`. A question runs
`~/.claude/bin/grip-recall ask "<question>"`, with `--absence`, `--key`, `--why`, `--deep`,
`--json` and `--timings`.
