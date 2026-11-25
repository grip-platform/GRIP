---
name: next
description: The canonical "go to next session" handoff — ONE command for the end-of-session ritual when auto-compact is OFF. Settles current work, then runs the existing handoff trio (/ultraprompt → /save → /ultraclear) in order and tells you it is safe to /clear then /exit. A thin sequencer — reimplements nothing; each sub-skill owns its own behaviour and honest degradation. Use for "/next", "go to next session", "wrap up and exit", "handoff and exit", "finish up, save, and let me exit".
status: Active
effort: low
version: 1.0.0
user_invocable: true
invocable_name: next
command: next
triggers:
  - /next
  - go to next session
  - wrap up and exit
  - handoff and exit
  - hand off to the next session
see_also:
  - ultraprompt
  - saving-state
  - ultraclear
  - handoff-prompt
  - save-exit
  - closeout
  - afk
---

# /next — go to the next session

## What it is (plain English)

When auto-compact is **off** and you are wrapping up to move to a fresh session,
you normally type a manual sequence like *"finish it, then write an ultraprompt
for continuation, then /save, then I want to /exit."* `/next` is that whole
ritual as **one command**, in the right order, with a clear "safe to exit" close.

It is a **thin sequencer**, not new machinery — it invokes skills that already
exist (the **handoff trio**) plus a settle step. It reimplements nothing, and it
does **not** re-explain what each sub-skill does or duplicate its degradation
handling — each sub-skill owns that. `/next`'s only job is calling them **in the
right order**. (Honesty note, AGORA #3965: the settle step's two read-only git
probes are the ONE bespoke behaviour `/next` owns itself — they are owned here
and cited nowhere else.)

## The sequence (order is load-bearing)

The brief and the save must happen **while context is still hot — before the
window is cleared** (writing an ultraprompt after `/clear` is too late). The
canonical rationale for this ordering lives in **`skills/ultraprompt/SKILL.md`
§"Composition — the session-handoff trio"** — read it there, it is not restated
here (one home, no drift; if that section ever moves, the one-line summary is:
*brief and save must precede the clear — write while hot*).

1. **Settle** (main thread) — finish or explicitly park the current unit of
   work so the brief describes a clean state. `git status --short` to see what is
   in flight, and `git log --branches --not --remotes --oneline | head -5` for
   unpushed commits — **unpushed work → run `/closeout` first** (this checks the
   precondition the siblings section states; AGORA #3965). Nothing in flight →
   say so, move on.
2. **`Skill(skill="ultraprompt")`** — write the next session's continuation
   brief while context is hot, and register the `/clear`-surviving pointer task.
3. **`/save`** — `grip-py ~/.claude/lib/grip_save.py "<achievement>"` (achievement
   optional; auto-detected from git log).
4. **`Skill(skill="ultraclear")`** — partition + cheap-serialise the checkpoint so
   the next session warm-reloads only the small HOT bundle.
5. **Minimal handoff prompt** (the final output) — generate the MINIMAL paste-in
   prompt and copy it to the clipboard, via the shared lib (DRY — reimplemented
   nowhere; it POINTS at the `/ultraprompt` brief, never duplicates it):
   `python3 ~/.claude/lib/next_session_prompt.py --next "<one-line next action>" --brief "<ultraprompt path>" --task <pointer>`
   It prints the prompt AND copies it cross-platform (pbcopy/xclip/wl-copy/clip.exe;
   graceful no-op if none). Relay its `copied to clipboard — paste into the new
   session` line. See `skills/handoff-prompt/SKILL.md`.
6. **Close** — print the safe-to-exit line (below).

## Close (final turn text)

```
Handoff complete — safe to move on.
  Brief:  <ultraprompt path>  (task #<pointer>)
  Saved:  <n>/<total> subsystems
  Clear:  <checkpointed | is_noop — /clear optional>
  Prompt: copied to clipboard — paste it as the first message next session.
  → run  /clear  then  /exit.  Next session warm-reloads the HOT bundle + brief.
```

Each value is relayed from its owner's own output (the data-flow contract,
AGORA #3965): `<ultraprompt path>` + `#<pointer>` = the `plans/ULTRAPROMPT-*.md`
path and pointer-task number `/ultraprompt` reports when it writes the brief;
`<n>/<total> subsystems` = the pass-count line `lib/grip_save.py` prints;
`<checkpointed | is_noop>` = the outcome `/ultraclear` returns. Relay verbatim —
if a sub-skill did not report a value, write `unknown` rather than inventing one.

## Honesty (the one rule /next itself owns)

`/next` reports each step's **real** outcome and never fakes a handoff — but it
does so by **surfacing what the sub-skills return**, not by re-implementing their
checks. If `/ultraclear` returns `is_noop` (small window), say `/clear` is
optional; if a `/save` subsystem failed, name it; if nothing was in flight, say
the settle was a no-op. The sub-skills degrade honestly on their own — `/next`
just relays it.

## Distinct from its siblings (no overlap)

- **`/afk`** = *unattended autonomous continuation* — you step away and GRIP keeps
  **working** (the `/auto /rsi` loop) until `/back`; the session stays live.
  `/next` is the opposite: it **winds the session down** for a clean exit.
- **`/save-exit`** = save + a goodbye banner, but **no continuation brief and no
  window checkpoint**. `/next` adds the `/ultraprompt` and `/ultraclear` legs.
- **`/closeout`** = the git-commit + push + Slack end-of-session *ceremony*.
  `/next` is the *context handoff*, not the shipping ceremony (run `/closeout`
  first if you have unpushed work).

## Provenance

Composes `skills/ultraprompt`, `lib/grip_save.py` (/save), and `skills/ultraclear`
(+ `lib/ultraclear.py` / `lib/ultraclear_session.py`). Origin: GRIP issue #3950
(V>> 2026-07-13, "the go-to-next-session ritual should be one command"). Designed
via the `canonical-next-session-handoff` workflow and cut from a 10KB draft to
this thin pointer by its KISS-adversary (verdict OVERBUILT → the trio ordering
has one home in `/ultraprompt`; do not copy it here).
