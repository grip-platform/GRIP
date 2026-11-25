---
name: afk
description: Away From Keyboard mode. The operator types /afk and steps away; GRIP keeps working autonomously (the /auto /rsi spirit — pick the next valuable safe work, execute, verify, continue) until the operator returns and types /back. Composes grip-yolo (halting-gate relaxation) + the autonomous loop; NEVER authorises irreversible/destructive or cross-client-commercial actions unattended. Use for "/afk", "I'm stepping away, keep going", "/back", "I'm back".
---

# /afk — Away From Keyboard

## What it is (plain language)

You step away from your desk. Instead of GRIP sitting idle waiting for you,
`/afk` tells it: *keep going on the useful, safe work — I'll be back.* When you
return you type `/back`, and GRIP hands you a short "while you were away" summary
of what it did.

It is a **thin orchestration** over machinery GRIP already has — it invents
nothing new, it just wires three existing pieces together:

1. **A per-session marker** (`state/session/afk-<sid>-mode.json`, keyed by
   `CLAUDE_CODE_SESSION_ID`) every surface can read to know *"operator away,
   autonomous continuation authorised."* Its lifecycle is owned by the generic
   session-mode shim (`bin/grip-mode <name> …`, `lib/session_mode.py`): atomic
   writes, TTL honoured on read (an expired marker reads INACTIVE and is
   reaped), audit log at `logs/afk-<sid>-mode.log`. `grip-afk` never hand-rolls
   the marker. **The marker is session-scoped so N concurrent AFK sessions never
   share one global `afk` marker** — see the pile-on fix under Provenance. (No
   session id → the global `afk` name, backward-compatible.)
2. **`grip-yolo`** — relaxes the *halting* gates (the ones that pause to ask
   "are you sure?") so GRIP proceeds without you at the keyboard. Quality gates
   (DRY/KISS/CC/no-stubs) and safety gates **still fire**.
3. **The `/auto /rsi` loop** — the "pick the next valuable work, do it, verify,
   continue" behaviour, run continuously per `rules/rsi-loop-no-yield.md` (don't
   stop to "yield" between waves).

## The safety core — what /afk NEVER does unattended

`/afk` relaxes *hesitation*, never *safety*. The four HALT classes from
`rules/operational-context.md` still stop and wait — they are collected into a
"needs you" list surfaced on `/back`, never done blind:

1. **Irreversible / destructive ops** — force-push, prod DB drop, deleting a
   memory/rule, public post under the operator's identity.
2. **Direction-A-vs-B forks** with materially different outcomes.
3. **Genuine operator-only information gaps** (things only the human knows).
4. **Cross-client commercial decisions** (pricing, terms).

Force-push and remote-history rewrites never pass (inherited from `grip-yolo`).
The session runs under a **fixed 12h TTL** (43200 seconds; the clamp is enforced
by the session-mode shim, `lib/session_mode.py`) so it can never run forever.

## Usage

| Command | Effect |
|---|---|
| `/afk` | Operator away → GRIP continues autonomously until `/back`. |
| `/afk keep shipping the docs` | Same, with a note recorded in the marker + audit log. The agent forwards everything after `/afk` verbatim as the note (`grip-afk on "keep shipping the docs"`) — the agent reading `commands/afk.md` IS the parser. |
| `/back` | Operator returns → clear the marker, restore interactive control, summarise what happened while away. |

Under the hood (`bin/grip-afk`):

```bash
grip-afk on   ["note"]  [ttl_seconds]   # default ttl 43200 (12h)
grip-afk off                            # alias: back
grip-afk status
```

## Behaviour contract

- On `/afk`: run `bin/grip-afk on "<note>"`, confirm the marker is written, then
  **immediately begin the next valuable safe work** — do not end the turn to
  "wait" (that would defeat the purpose; `rules/rsi-loop-no-yield.md`).
- While AFK: bias to external/verifiable value (`rules/loop-priority-external-over-theatre.md`),
  commit + push atomically (`CLAUDE.md` Commit-Often), never touch the HALT classes.
- On `/back`: run `bin/grip-afk off`, then output a short **"While you were away"**
  digest — what shipped, what's still open, and anything deferred because it hit a
  HALT class and needs the operator. The digest is **agent-produced** (like `/eod`
  and `/recap`), anchored on the `AFK window: <started> -> <now>` line `grip-afk
  off` prints: `git log --since "<started>"` recovers Shipped mechanically even if
  the conversation was compacted mid-AFK.
- **Resume THIS session's own intent, never the shared backlog.** `grip-afk off`
  prints an `AFK intent (resume THIS session's own work): <note>` line — the note
  THIS session recorded on its own `/afk`. Resume THAT. Do **not** grab the next
  item off the global task list or another concurrent session's branch: when the
  operator `/back`s several sessions at once, each must resume its *own* AFK
  intent, or all N pile onto the same task (the exact failure the session-scoped
  marker fixes). No intent line printed (none recorded) → fall back to the digest
  + this session's own context, still never the shared queue.

## When someone else is at the keyboard

If the operator hands the keyboard to another person (as V>> did for a guest on
2026-07-10), `/afk` marks the operator away and authorises autonomous
continuation, **but a present human's prompts always take priority** — GRIP stays
responsive to whoever is typing and continues useful work between prompts. `/back`
remains the *operator's* command to reclaim interactive control.

## Provenance

Origin: V>> 2026-07-10, live, handing the keyboard to a guest — *"`/afk` … GRIP
should continue while I'm away from the keyboard in /auto /rsi mode until I invoke
another new command `/back`."* Built live for a guest to witness. Composes
`skills/auto`, `skills/rsi-optimal`, `bin/grip-yolo`, and the HALT-class discipline
in `rules/operational-context.md`. Test: `tests/test_grip_afk.py`.

Rebased 2026-07-13 onto the generic session-mode shim (the grip-mode
consolidation, PR #3953): marker lifecycle delegated to `lib/session_mode.py` —
the shim was extracted FROM this skill's first hand-rolled `bin/grip-afk` (YSH,
N=3) and fixes its staleness bug (a stale marker file used to read ACTIVE
forever; it now expires at its TTL deadline and is reaped).

Session-scoped 2026-07-13 (the pile-on fix): the operator `/back`ed six
concurrent sessions and all six resumed the SAME work — the marker was a single
global `state/session/afk-mode.json`, so every `/back` read the same marker and
the same shared backlog. The mode name is now keyed by `CLAUDE_CODE_SESSION_ID`
(the per-session id the harness exports to Bash — NOT `CLAUDE_SESSION_ID`, which
is unset there; `CLAUDE_SESSION_ID` is kept only as a compat fallback), as
`afk-<sid16>` (16 hex chars = 64-bit, so distinct sessions colliding onto one
marker is negligible for any realistic count). Each session owns its own marker
and resumes only its own recorded intent. Backward-compatible: no session id →
the global `afk` name. Test: `tests/test_grip_afk_session_scoped.py`
(Goodhart-proof against the scoping revert — reverting the scoping collapses two
sessions onto one marker and fails).
