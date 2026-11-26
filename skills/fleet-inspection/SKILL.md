---
name: fleet-inspection
description: Lands every running GRIP session's uncommitted work through PRs, then shows the operator what each session and GRIP as a whole did, on one short local page. Covers minting a run and its landing prompt, waiting for every session's record, checking each record's claims against GitHub, and writing the short judged summary (one-line tldr, at most six things that need the operator, a plain-words paragraph) that the page leads with. Use when the operator wants every session to finish off and land its work, or asks to inspect what the whole fleet did.
status: Active
effort: medium
version: 1.0.0
created: 2026-10-02
updated: 2026-11-26
user_invocable: true
command: fleet-inspection
triggers:
  - /fleet-inspection
  - "land every session's work"
  - "inspect what every session did"
integrates:
  - ultraprompt
  - prove-it
  - mesh
see_also:
  - converge-fleet
  - grip-fleet-quit
  - mesh
---

# Fleet inspection

The operator runs many sessions at once, across harnesses. Asking each one by hand to
land its work, then reading eleven reports, is slow and easy to get wrong. This skill
does it in three steps, with one tool, `bin/grip-fleet`, and one page at the end.

"Fleet" here means the running sessions. `/converge-fleet` is something else: parallel
expert lanes inside one converge loop.

## The three things kept apart

| Claim | Where it comes from | What settles it |
|-------|---------------------|-----------------|
| What a session says it did | its record | nothing yet: a record is a claim |
| What actually landed | GitHub | `grip-fleet inspect` checks every PR a record says it landed |
| What the operator decided | the operator | the summary marks a need `decided`, with what was decided |

## Workflow

1. **Mint a run.** `grip-fleet prompt --expect N [--since 2026-10-01] [--note "..."]` makes
   `<GRIP>/tmp/fleet-landing/<run>/`, renders the landing prompt for it from
   `config/fleet-landing-prompt.md` and copies it to the clipboard. N is how many sessions
   are running. A note is a fact every session should read before landing.
2. **Deliver the landing prompt over the mesh** (`skills/mesh/SKILL.md`, "Inform all
   sessions: queue, wake, report") instead of asking the operator to paste it into every
   session by hand.

   1. Queue it with grip-presence's `session_broadcast` (`from_name` your harness,
      `body` the prompt, `kind: "broadcast"`, `from_id` your own session id so you are
      left out).

   2. Wake every local peer. Call `ListAgents`, then send one `SendMessage` per peer: a
      self-contained first line, the operator's request quoted, the prompt verbatim, and
      `notify_when_idle: true`.

   3. Report per session: sent, held for approval, refused, or unreachable. Name any peer
      shown as `waiting` -- it may need the operator to answer an open prompt first.

   Hand-pasting stays the fallback for a session the mesh cannot reach: another harness,
   another machine, or a session absent from `ListAgents`. A session reported held or
   refused is the operator's own call, not an automatic paste that runs past why it held
   or declined.

   Each session lands its own work through a worktree and a PR, never by writing in the
   shared tree, then writes ONE record with six fixed headings (who, landed, work this
   session, unfinished, needs V>>, left uncommitted). The headings are what the parser
   reads, so the template pins them.
3. **Wait.** `grip-fleet wait` (in the background) prints each record as it arrives and
   exits 0 when all N are in, or 3 at the timeout.
4. **Read every record, then write `summary.json` in the run folder.** This is the judged
   layer, and it is yours: a `tldr` (one line, at most 220 characters), at most six
   `needs` (each with `text`, `sources` naming the sessions that raised it or
   `"inspection"` for your own findings, `status` open or decided, and a `decision` when
   decided), an optional `eli5` paragraph (at most 650 characters), and optional one-line
   `sessions` entries. Write it plainly (/tldr, /plain-language, /eli5): a first-time reader
   must follow it in one pass. `grip-fleet check-summary` validates it.
5. **Render.** `grip-fleet inspect --open`. The page leads with the summary, then one line
   per session (each record folded underneath), then the GitHub check, then every request
   the sessions made, folded. A summary can shorten the page but never hide a request.
6. **Record what the operator decides** by marking the need decided and rendering again.

## Traps worth naming

- **A session writing in the shared tree.** A Kiro session committed and rebased in
  `~/.grip` and pushed straight to main (a3b78237c); its harness entry turned every PR's
  anchors red for half an hour. The prompt forbids direct pushes from any harness, because
  GRIP's own push guard lives in Claude Code's hooks and other harnesses never see it.
- **Settings rewritten from elsewhere.** A merge in any worktree used to re-run the
  settings merge on production (GRIP#7038). Treat a changed `settings.json` as a finding.
- **The merge sweep.** PRs can merge within a minute of AGORA's verdict, before slower
  checks finish. A record saying "merged" is checked, not trusted.
- **Personal matters.** Records name them in one neutral line. The page is local: never
  publish it, and never paste a record into a public surface.

## Example: 2 October 2026

The first run, by hand: eleven records, about 89 merged PRs across GRIP and HAL, 33
requests for the operator. The hand-built page was 109 KB and its findings were
hand-written. This skill is that run made repeatable; `grip-fleet inspect` on the same
records gives the same counts.

## Falsification

Wrong if, over the next few runs, the operator still has to open every record to find what
needs him (the summary is not doing its job), or a landed claim the check passed turns out
not to have landed (the check is too weak). Track requests the operator found only in the
folded list, and landed claims later found false. Never track how often the skill runs.
