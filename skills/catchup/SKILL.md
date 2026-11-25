---
name: catchup
description: Reconstruct YOUR context from the real record, not from recollection. Produces a chronology of everything asked, what shipped, what did not, and the one gap you would otherwise miss — as markdown plus a branded PDF, backed up. Use when the operator says "/catchup", "whoa update me", "give me the full context", "I'm falling behind", "catch me up", or at session close so the next session inherits full context with zero effort.
---

# /catchup — Context Transfer

## The problem this exists for

AI systems improve recursively; the human's context does not keep pace. The
failure is not that the AI forgets anything — it is that **the operator loses
the thread of what he asked and what was done**, faster than the work generates
it. Ten directives in one session, each answered, none rolled up.

That asymmetry is not capability. It is **transfer**. `/catchup` is transfer as
a first-class artefact.

## What it is — and what it is not

**Not** a summary. A summary is prose from memory. A catch-up is a
**reconstruction**: every claim traced to an artefact — git, PRs, ledgers, live
probes, file contents — never to the model's recollection of the conversation.

The rule that makes it trustworthy: **if it cannot be shown, it is not claimed.**
Where something is unknown, the document says unknown. Where a claim is the
operator's own report rather than a record, it says so.

## The six parts (all six, every time)

1. **Chronology** — every request, in order, verbatim where possible, with what
   it produced and its outcome. This is the spine.
2. **Done / Not-Done / Blocked** — three lists. A blocked item MUST name what
   unblocks it, or it is not a blocker, it is an omission.
3. **Goal ledger** — the operator's stated goals mapped to evidence, status
   measured from artefacts. Not "believed done" — *shown* done.
4. **Decision log** — what was decided, on what evidence, and **what would
   falsify it**. A decision without a falsifier is a preference.
5. **Skill manifest** — which skills actually ran, which were only referenced,
   and **which were asked for but not delivered**. This part is deliberately
   uncomfortable: it is where the system admits the requests it dropped.
6. **The honest gap** — the one line the operator would otherwise miss. Singular
   and named. If the gap cannot be named in one sentence, the catch-up has not
   done its job.

## Output contract

- `~/GRIP-CHRONICLE-<YYYY-MM-DD>.md` — editable source.
- `~/GRIP-CHRONICLE-<YYYY-MM-DD>.pdf` — branded, holdable, readable away from a
  terminal. Render via `weasyprint` on a styled HTML wrapper (A4, system-font
  Swiss-minimal, page numbers, tables bordered).
- Both copied out of the repository with `bin/grip-backup` the moment they are
  produced (`rules/paramount-backups.md`). A chronicle that can vanish is not a
  record.

## Cadence

- **On demand** — when the operator feels the thread slipping.
- **At `/save`** — emitted automatically at session close, so the next session
  inherits full context without anyone asking.

## Composition (reimplements nothing)

| Needs | Reaches for |
|---|---|
| The session's own requests and outcomes | the live conversation, `git log`, `gh pr list` |
| What shipped, verifiably | merged PRs + `git show origin/main:<path>` probes |
| Live system state | `launchctl print`, health probes, ledger line counts |
| Delivery and backup | `bin/grip-backup`, `weasyprint` |
| Decisions the operator must make | `/hitl` — never decided silently |

## Dogfood (its first run)

Its first run was the 15 September 2026 session: five merged PRs, a live
WhatsApp evidence monitor, and a deliberate non-ship. That run surfaced its own
missing parts — the catch-up family (`reasoning-audit`, `aurora`,
`whoa-update-pls`, `recap`, `standup`) had not been invoked as commands, and the
skill manifest is where that became visible rather than lost. **A catch-up that
cannot report its own gaps is not doing the job.** Keep part 5 honest.

## Anti-patterns

- **Prose from memory.** If the chronology is not traceable, it is fiction with
  dates on it.
- **Flattery.** "Great progress!" costs the operator the trust he needs for the
  next catch-up. Report the gap.
- **Omitting the undelivered.** The dropped requests are the most valuable part,
  because they are the ones he cannot see for himself.
- **A gap list that is not singular.** Six gaps is a backlog; one gap is a
  decision.

## Provenance

V>> 2026-09-15: *"so that **I** can gain full context as it's now a fact that AI
is Recursively Self Improving but humans are falling behind."* Shaped by HITL —
output and cadence chosen by the operator, not assumed. Composes `rules/
canonical-output-style`, `rules/paramount-backups`, and `/hitl`.
