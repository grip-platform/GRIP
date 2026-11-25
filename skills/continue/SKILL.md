---
name: continue
description: Reconstructs the goal, approval and next step of the current session from the transcript itself -- the newest real request before the "continue" prompt, whether a later short reply approved a specific step, and whether that step's own referents (a PR, a path, a URL) already show it done -- so the operator never has to re-explain the session after a compaction or an interrupt. Use when /continue is invoked, or the operator asks "continue", "what was the goal", "what was the task", or "where were we".
status: Active
version: 1.0.1
created: 2026-11-10
effort: low
allowed-tools: Bash(~/.claude/bin/grip-continue-card:*), Bash(echo:*)
triggers:
  - /continue
  - continue
  - what was the goal
  - where were we
integrates:
  - task-reconcile/SKILL.md
see_also:
  - recall
  - nextmove
  - next
  - ultragoal
  - task-reconcile
---

# /continue — reconstruct the goal, never ask for it

## The name, measured

Claude Code 2.1.319 defines `/continue` as an ALIAS of the built-in `/resume`
(the bundle registers `name: "resume", ..., aliases: ["continue"]`). Its
lookup is `ti(e, n) = n.find(y => y.name === e) ?? firstAliasMatch`, so an
exact skill **name** wins over a built-in **alias** — a skill named
`continue` runs, and `/resume` still opens the session picker untouched.
Confirmed live on 2026-11-10: `claude -p "/continue"` ran this skill.

## Why

The operator types "continue. what was the goal/task again? /recall" after a
compaction or an interrupt rather than re-explaining the session by hand.
`/continue` replaces that whole prompt with one word and does the same job
the same way every time: read the transcript, not the operator's memory.

## What it does

`bin/grip-continue-card` (over `lib/continue_card.py`) makes one streaming
pass over the transcript and renders a seven-field card:

- **GOAL** — the newest real request, or a closer's step when a newer short
  reply ("yes", "lgtm", "merge it") approved it. The "/continue" row itself
  never counts; any other newest row (a request, a "yes") does, because the
  harness may run the card before it writes the "/continue" row.
- **APPROVED** — the reply and the step it approved, only when that applied.
- **STATE** — every referent (PR, issue, path, URL, repo, or a command such as
  `gh pr merge 9069 --repo ...`) in GOAL and NEXT, probed for real via
  `lib/task_reality_reconcile.py` (DONE / OPEN / UNKNOWN, never a guess), plus
  the worktree's own branch/dirty/unpushed/behind-main state.
- **NEXT** — the `RECOMMENDED NEXT STEP (...)` closer the "/continue" row
  answered (the newest closer in the file), with its owner; flagged "already
  done" when its own referents confirm it, with what the record shows after it.
- **SUMMARY** — the newest compaction summary's own `Next:` paragraph, if any.
- **SOURCES** — exactly what was read and what was not, and why.
- **CONFIDENCE** — HIGH / MEDIUM / LOW with a one-line reason.

## Procedure

1. The card is injected at skill load with:

   ```text
   !`~/.claude/bin/grip-continue-card --session "${CLAUDE_SESSION_ID}" || echo "CARD UNAVAILABLE -- run grip-continue-card with Bash"`
   ```

   Claude Code checks this command against its Bash permissions before it
   runs, and a refusal aborts the whole skill before its first turn. Measured
   on 2026-11-10 in default permission mode: a `${CLAUDE_DIR:-...}` path was
   refused ("a variable in braces ... can't be checked before it runs"), and
   the plain `~/.claude` path was refused until the `allowed-tools` grant in
   this file's frontmatter covered it. `~/.claude` is where Claude Code loads
   this skill from, so it names the same install. `${CLAUDE_SESSION_ID}` is
   substituted before the check; if it comes back empty, the engine falls
   back to `CLAUDE_CODE_SESSION_ID` / `CLAUDE_SESSION_ID`. A command that
   fails after it starts would also abort the skill, so the line ends in
   `|| echo`. If the card reads CARD UNAVAILABLE, or no card appears at all
   (another harness), run `~/.claude/bin/grip-continue-card` with Bash before
   doing anything else.

2. State the goal in **at most three plain sentences**: what was asked
   (quote the operator's own words), where it stands (the checked STATE —
   not the raw probe output), and the next step with its owner. **Never ask
   the operator what the goal was** — that is the one thing this card exists
   to make unnecessary.

3. Act on it:
   - `(mine)` — do it now, in this turn.
   - `(yours)` — give the command alone on its own line and stop.
   - A named third party — say who, then carry on with any open `(mine)`
     work; neither waiting nor working changes a block on someone else.
   - When NEXT is already done, take the next open item the record shows
     after it; if there is none, say so plainly and stop rather than
     inventing work.

4. **Deep path.** When CONFIDENCE is LOW, the sources conflict with what the
   operator just said, or the operator wrote `--deep` or "ultracode": run the
   saved workflow `continue-deep` with `args: {session_id, transcript_path,
   cwd, card_json}` (`card_json` is this card's own `--json` output), arm
   `bin/grip-fleet-watch` in the same turn, and act on its reconciled card
   instead of the fast one above.

## Example (a real-shaped card)

```text
GOAL        "Create lib/continue_card.py with tests" (this session)
STATE       lib/continue_card.py PATH EXISTS (file check) | worktree feat/continue-skill: 0 dirty, 2 unpushed, 0 behind origin/main (vs last fetch)
NEXT        (mine) write bin/grip-continue-card
SUMMARY     no compaction summary in this transcript
SOURCES     read: transcript b759da7c (1,842 lines), closers, git; not read: ultragoal (no public call discovers an armed goal without its path)
CONFIDENCE  HIGH -- goal and next step both come from this session; the next step names nothing a probe can check
```

Response: *"You asked me to create `lib/continue_card.py` with tests. It
exists and the worktree has 2 unpushed commits. Next (mine): write
`bin/grip-continue-card`."* Then write it — no question asked.

## Limits (read this before trusting a LOW or MEDIUM card)

- **The trigger is a fixed list of wordings, not free-form language
  understanding.** "/continue" (with `--deep` or `ultracode`), "continue",
  "/recall", and the three "what was the X" / "where were we" phrases (alone
  or combined in one message) are recognised; an unlisted paraphrase of
  "continue" falls through and reads as a REQUEST instead, which is the safe
  direction to fail in.
- **The kept window is the last 30 operator rows.** A REQUEST further back
  than that falls through to the compaction summary, the previous session's
  transcript, then recall — each a step down in confidence, in that order.
- **The armed-ultragoal fallback is not implemented.** Nothing in
  `lib/ultragoal.py` discovers an armed goal without already knowing its
  brief path or slug, so this step does not run; SOURCES says so.
- **STATE depends on `gh` being authenticated** unless `--no-probe` is set
  (then referents read UNKNOWN by name, and the worktree's own git state —
  branch, dirty, unpushed, behind-main — still renders; `--no-probe` skips
  gh/network, never git or a local file check).
- **Privacy**: the card quotes only operator rows and assistant closers —
  never tool output, hook output, or a memory body.

## Falsification

Wrong if, over the next ~10 uses after a compaction or an interrupt, the
operator corrects the stated goal more than once or goes back to the long
prompt. Track uses and corrections per use; never count how often it fires.
