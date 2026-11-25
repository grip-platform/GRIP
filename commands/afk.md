---
name: afk
description: Away From Keyboard. Operator steps away; GRIP continues autonomously (/auto /rsi spirit) on safe work until /back. Composes grip-yolo + the autonomous loop; never does irreversible/cross-client actions unattended.
---

# /afk

Invokes the `afk` skill — see `skills/afk/SKILL.md`.

1. Run `bin/grip-afk on "<note>"` — where `<note>` is **everything the operator
   typed after `/afk`**, verbatim (e.g. `/afk keep shipping the docs` →
   `bin/grip-afk on "keep shipping the docs"`; bare `/afk` → empty note). The
   agent reading this file IS the parser — no other note pipeline exists.
   Confirm the marker at `state/session/afk-mode.json` was written.
   **If `grip-afk on` exits non-zero, do NOT proceed autonomously** — the safety
   model depends on the marker; surface the error to the operator instead
   (AGORA #3964).
2. **Immediately begin the next valuable, safe work** — do not end the turn to
   "wait" (`rules/rsi-loop-no-yield.md`). Continue wave after wave until `/back`.
3. Stay within the safety core: the four HALT classes
   (`rules/operational-context.md`) are collected into a "needs you" list for
   `/back`, never done blind. Force-push never passes.

If a human is present at the keyboard (e.g. the operator handed it over), their
prompts take priority; GRIP continues useful work between them.

Synonyms: `I'm stepping away, keep going`, `away from keyboard`.
