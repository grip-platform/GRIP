---
name: mob-done
description: Guarded wrapper over `mob done` -- the Guild mob.sh command that collapses the work-in-progress branch back onto the base branch at the end of a session. Use when the mob session is finished and it is time to write one real commit together.
status: Active
version: 1.1.0
triggers:
  - /mob-done
  - "end the mob session"
  - "finish the mob"
---

# /mob-done -- guarded `mob done`

Runs two guards, then execs the real `mob done`, which collapses the WIP
branch back onto the base branch and leaves all the changes staged but
uncommitted, ready for the mob to write one real commit message together.

## Guards

| Guard | Refuses when | Fix |
|---|---|---|
| mob on PATH | `mob` is not installed | `brew install remotemobprogramming/brew/mob` |
| not main/master | the current branch is `main` or `master` | `git checkout -b <topic>` first (an ordinary name -- never one starting with `mob/`, which is mob's own WIP-branch prefix) |

No dirty-tree or test guard here: `mob done`'s entire job is to leave the
collected changes staged-but-uncommitted, so there is nothing to refuse
about the tree's state at this point.

## Usage

```bash
bin/grip-mob-done
# then, as a mob:
git commit -m "Add checkout total calculation with mutation-tested edge cases"
git push
```

Any arguments are passed straight through to `mob done`.

## Example

```text
$ bin/grip-mob-done
<mob's own output: WIP branch collapsed, changes staged on the base branch>
$ git commit -m "..."
$ git push
```

## Related skills

| Skill | Relationship |
|---|---|
| `mob-doctor` | run first, to confirm the machine is ready |
| `mob-start` | take the keyboard |
| `mob-next` | hand over the keyboard |
