---
name: mob-start
description: Guarded wrapper over `mob start` -- the Guild mob.sh command that creates or joins the work-in-progress branch. Use when taking the keyboard at the start of a Guild mob-programming rotation.
status: Active
version: 1.1.0
triggers:
  - /mob-start
  - "start the mob session"
  - "take the keyboard"
---

# /mob-start -- guarded `mob start`

Runs two guards, then execs the real `mob start`, so the timer and branch
creation behave exactly as upstream `mob.sh` (https://mob.sh) documents.

## Guards

| Guard | Refuses when | Fix |
|---|---|---|
| mob on PATH | `mob` is not installed | `brew install remotemobprogramming/brew/mob` |
| clean tree | the working tree has uncommitted changes | commit/stash, or pass `--include-uncommitted-changes` to real `mob` yourself |

**`mob-start` ALLOWS `main`/`master`** (V>> decision, GRIP#9266 follow-up),
matching upstream mob.sh's own documented flow, which starts `mob start`
from `main`. An earlier revision of this skill added a stricter "never on
main/master" guard to all three wrapper commands; V>> narrowed that to
`mob-next` and `mob-done` only -- see their SKILL.md files. Starting a
session from `main` is normal; it is handing over or finishing one directly
against `main`/`master` that stays refused.

## Usage

```bash
bin/grip-mob-start            # create/join the WIP branch
bin/grip-mob-start 10         # same, with a 10-minute rotation timer
```

Any arguments are passed straight through to `mob start`.

## Example

```text
$ git status --short
 M some-file.txt
$ bin/grip-mob-start
[grip-mob-start] refused: the working tree has uncommitted changes
    fix: commit or stash them, or pass --include-uncommitted-changes to mob yourself if you truly want them carried in
$ git commit -am "wip"
$ bin/grip-mob-start 10
<mob's own output: WIP branch created, timer started>
```

## Related skills

| Skill | Relationship |
|---|---|
| `mob-doctor` | run first, to confirm the machine is ready |
| `mob-next` | hand over the keyboard |
| `mob-done` | end the session |
