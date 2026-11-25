---
name: handoff-prompt
description: Generate a MINIMAL next-session continuation prompt (the specific next action + task/artifact pointers, <=400 chars) and auto-copy it to the system clipboard, so the operator pastes ONE line into the new session. Runs as the final step of /next and /save; also invocable standalone. Use for "/handoff-prompt", "copy the next-session prompt", "give me the paste-in resume line", or after /save when the resume line should be on the clipboard.
status: Active
effort: low
version: 1.0.0
user_invocable: true
invocable_name: handoff-prompt
command: handoff-prompt
triggers:
  - /handoff-prompt
  - copy the next-session prompt
  - paste-in resume line for the new session
  - minimal handoff prompt
see_also:
  - next
  - saving-state
  - ultraprompt
  - ultraclear
---

# /handoff-prompt — the minimal paste-in prompt for the next session

## What it is (plain English)

When you wrap up a session, warm-start already reloads CLAUDE.md, the holistic
memory kernel, and `rules/session-context.md` on the next launch. So the *only*
thing the next session is missing is **one line**: the specific next action and
pointers to the brief/tasks/artifacts. `/handoff-prompt` produces exactly that
line — **minimal** (<=~400 chars) — and **copies it to your clipboard** so you
paste it as the first message next session.

It is the **final output** of `/next` and `/save`. You can also invoke it on its
own any time you want the resume line back on your clipboard.

## Why minimal — it composes with warm-start, never duplicates the brief

The full working-state brief is `/ultraprompt`'s job (it lands in
`plans/ULTRAPROMPT-*.md` and is pointed at by a pointer task). `/handoff-prompt`
does **not** repeat it — it **points at it**. The prompt is a short pointer that
rides on top of the auto-loaded context, so a paste is one line, not a wall.

## How to run it (DRY — one shared lib, never inlined)

The whole behaviour lives in `lib/next_session_prompt.py`. `/next` and `/save`
call it as their last step; you can call it directly too:

```bash
# Explicit next action + a pointer at the /ultraprompt brief:
python3 ~/.claude/lib/next_session_prompt.py \
  --next "wire the receipt verifier into CI" \
  --brief "plans/ULTRAPROMPT-receipt-ci.md" --task 537

# Bare — auto-gathers the next action from rules/session-context.md:
python3 ~/.claude/lib/next_session_prompt.py

# Print only, do not touch the clipboard:
python3 ~/.claude/lib/next_session_prompt.py --no-clip
```

It **prints** the prompt to stdout and **copies** it to the clipboard, then
emits `copied to clipboard - paste into the new session` on stderr. Relay that
line to the operator.

**Safety gates.** The clipboard step delegates to the canonical `grip_transfer`
transfer shim (DRY — no reinvented subprocess clipboard). Two gates ride on top:
**G1** — the programmatic `mint_and_deliver(state, clip=…)` defaults `clip=False`,
so a background / mid-loop `/save` checkpoint never clobbers the operator's
clipboard; only an interactive session-end handoff copies. **G2** — a brief path
under `drafts/<client>/` is dropped for its `task #id` pointer (and token-shaped
strings are scrubbed) so no client identity egresses via macOS Universal
Clipboard.

## What it generates

A single line, priority-ordered, truncated to the ceiling with the next-action
pointer ALWAYS kept:

```
Continue the GRIP session. NEXT: <one-line next action> Brief: <path> (task #N)
- read it first. Artifacts: <p1>, <p2>. (Warm-start already loaded CLAUDE.md +
holistic memory + session-context.)
```

State comes from (in order): auto-gather from `rules/session-context.md` →
`--from <state.json>` → explicit `--next / --brief / --task / --artifact` flags
(later sources win).

## Clipboard — cross-platform and fail-soft

Copies via the first tool on PATH: `pbcopy` (macOS), `wl-copy` or `xclip`
(Linux), `clip.exe` (Windows / WSL / Git-Bash). If none is found, or the copy
fails, it is a **graceful no-op**: the prompt is still printed, a plain-language
stderr note tells the operator to copy it manually, and it **never raises** — a
clipboard miss must never break a `/next` or `/save` handoff.

## Provenance and falsification

Origin: V>> 2026-07-18 — "the final output of a /next or a /save should be a
MINIMAL prompt to enter in the new session, and should be automatically copied
to the clipboard." Substrate: `lib/next_session_prompt.py`; tests:
`tests/test_next_session_prompt.py`.

Wrong if, over ~5 handoffs: the copied prompt makes the next session **cold-start**
(re-derive state the pointer + warm-start already carried → the prompt is too
thin, add the missing pointer), OR the prompt duplicates the `/ultraprompt` brief
(defeating "minimal" → tighten to pointers), OR the clipboard step is unreliable
enough that operators stop trusting it (→ fix the copy path, never silently
drop the note).
