---
name: ultraprompt
description: Write a self-contained next-session continuation brief that transfers working state (not narrative), so the next session reaches full context in one read. Part of the session-handoff trio.
---

# /ultraprompt

Invokes the `ultraprompt` skill — see `skills/ultraprompt/SKILL.md`.

Write the next session's continuation brief WHILE context is hot. Five parts, in order:
(a) header — run-mode + Pool-A/Pool-B routing; (b) WHERE WE ARE — every state claim carries its verify-canonical probe (SHA / PR state / council seal / hypothesis id + deadline / path + gitignore warning); (c) numbered TASKS — each with grounding (`file:line`/seal), done-criterion, and depth guidance; (d) HELD / GATES — operator decision vs autonomous (never launder a held publish into an auto-task); (e) GUARDRAILS TO CARRY — session-specific lessons + linked feedback memories.

Conventions: land it at `plans/ULTRAPROMPT-<topic>.md` (or next to the work); ALWAYS `TaskCreate` a `NEXT SESSION (<run-mode>): <goal>` task pointing at the file (the pointer that survives `/clear`); budget <= ~8KB (link files, do not inline them).

R0 on the artifact: every claim probe-able or marked `UNVERIFIED — probe first`; gloss every internal PR/task id; the brief is WRONG if the next session acts on a stale claim a listed probe would have caught.

Composition — the session-handoff trio, in order: `/ultraprompt` (write the brief) -> `/save` (persist subsystems) -> `/ultraclear` (partition + cheap clear, then native `/clear`).
