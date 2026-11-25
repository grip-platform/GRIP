---
name: mob-next
description: Guarded wrapper over `mob next` -- the Guild mob.sh command that hands the work-in-progress branch to the next typist. Use when your rotation timer ends and it is time to pass the keyboard.
status: Active
version: 1.1.0
triggers:
  - /mob-next
  - "hand over the keyboard"
  - "pass the keyboard"
---

# /mob-next -- guarded `mob next`

Runs three guards, then execs the real `mob next`, which commits whatever is
in the working tree as a WIP commit and pushes it. **By upstream mob.sh's own
default (`--stay`, confirmed against the installed `mob --help`), it then
stays on the WIP branch** -- it does NOT return you to the base branch unless
you pass `--return-to-base-branch` (`-r`) yourself. An earlier revision of
this page claimed the opposite, copying the Guild onboarding page's prose
without checking it against the actually-installed tool (Codex review,
GRIP#9266): that page's claim does not match mob.sh 5.4.3's documented
default. If you want to land back on the base branch after a handover, pass
`-r` through to the wrapper.

## Guards

| Guard | Refuses when | Fix |
|---|---|---|
| mob on PATH | `mob` is not installed | `brew install remotemobprogramming/brew/mob` |
| not main/master | the current branch is `main` or `master` | `git checkout -b <topic>` first (an ordinary name -- never one starting with `mob/`, which is mob's own WIP-branch prefix) |
| repo tests pass | the repo's own `npm test` (read from `package.json`) fails, or `package.json` exists but cannot be parsed | fix the failing tests, or fix `package.json`, before handing over |

A dirty working tree is **not** refused here -- that is the whole point of
`mob next`: it commits whatever you were in the middle of, incomplete work
included. A repo with no configured test script is not blocked either; the
guard fails open on missing configuration and fails closed only on an
actual red suite (or a `package.json` that cannot even be parsed -- that is
not the same as "no test script configured").

## Usage

```bash
bin/grip-mob-next                       # stays on the WIP branch (upstream default)
bin/grip-mob-next --return-to-base-branch   # returns to the base branch after handover
```

Any arguments are passed straight through to `mob next`.

## Example

```text
$ bin/grip-mob-next
[grip-mob-next] refused: repo tests failed: FAIL src/checkout.test.ts
    fix: fix the failing tests before handing over
$ # fix the test, then:
$ bin/grip-mob-next
<mob's own output: WIP commit pushed; you stay on the WIP branch>
```

## Related skills

| Skill | Relationship |
|---|---|
| `mob-doctor` | run first, to confirm the machine is ready |
| `mob-start` | take the keyboard |
| `mob-done` | end the session |
