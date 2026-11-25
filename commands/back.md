---
name: back
description: Return from AFK mode. Clears the afk marker, restores interactive control, and prints a "while you were away" digest of what GRIP did.
---

# /back

Ends `/afk` (see `skills/afk/SKILL.md`).

1. Run `bin/grip-afk off` to clear this session's marker
   (`state/session/afk-<sid>-mode.json`) and disengage the halting-gate
   relaxation. It prints the **AFK window**
   (`AFK window: <started> -> <now>`), the durable digest anchor, followed by
   this session's recorded intent when there is one. When the marker lapsed
   before `/back`, the window still prints: its start comes from the session's
   audit log, `logs/afk-<sid>-mode.log`, and the line says so (GRIP#9244). If
   the line is absent or reads `partial` (a degenerate marker), fall back to the
   last `ON` timestamp in that log for the `git log --since` anchor
   (AGORA #3964).
2. Output a short **"While you were away"** digest. **The agent produces this**
   (there is deliberately no digest generator binary — same pattern as `/eod`
   and `/recap`): from session context, mechanically anchored on the AFK window —
   `git log --since "<started>"` recovers Shipped even if the conversation was
   compacted or cleared mid-AFK.
   - **Shipped** — what completed + landed (commits/PRs in the window, verify-canonical).
   - **Open** — what is still in flight.
   - **Needs you** — anything deferred because it hit a HALT class
     (irreversible / A-vs-B fork / operator-only info / cross-client commercial).

Synonyms: `I'm back`, `resume interactive`.
