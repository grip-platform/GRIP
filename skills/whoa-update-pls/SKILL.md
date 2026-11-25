---
name: whoa-update-pls
description: Hold-the-fuck-up status snapshot. Reads every signal across the live session and concurrent sessions, sorts it into three buckets (Live / Open-needs-closure / Backlog), explains each item in plain English with the why-what-how, then runs a HITL 1-2-3 wizard to close off items, delegate to the CodeTonight team, or defer. Built for V>>'s voice — no AI fluff, deeds not words, decisions get made.
status: Active
effort: medium
version: 1.0.0
triggers:
  - /whoa-update-pls
  - /ultrawhao
  - hold the fuck up
  - whoa what
  - what is going on right now
  - where are we
  - status snapshot
  - catch me up
integrates:
  - asking-users
  - hitl
  - creating-wizards
  - delegating-team-work
  - plain-language
  - authentic-writing
chains_to: delegating-team-work
see_also:
  - hitl
  - asking-users
  - creating-wizards
  - delegating-team-work
  - sprint
  - rsi-optimal
  - bg-dispatch
---

# /whoa-update-pls — Hold-the-Fuck-Up Status Snapshot

> **Subsumed into `/update-team --snapshot` (aka `--status`) — canonical.**
> `/whoa-update-pls` and `/ultrawhao` remain live aliases and still resolve
> here; the canonical entry is now `/update-team --snapshot`. This is an
> **interactive** mode (the HITL close/delegate/defer wizard) — foreground
> only, **never** on the scheduled `/update-team` tick. Body retained in full
> (reference + live implementation; archive-never-delete).

The "what the actual hell is happening, who is doing what, what's stuck,
what's done, what's mine to decide" command. One invocation, full picture,
plain English, decisions made before you leave.

**Aliases:** `/ultrawhao`. Both resolve here.

## When to use

You've context-switched, picked the laptop back up after a meeting, come
out of a long autonomous loop, walked back from the kitchen, or someone
just messaged you on Slack and you cannot for the life of you remember
what state the system is in. Run this. It tells you where everything is,
what wants your attention, and offers concrete decisions.

## What it produces

A single-pass output in three buckets, each item described in V>>'s
register: terse, plain, no fluff, British English, deeds not words.

### Bucket 1 — Live (in flight right now)

Anything actively running or blocked-but-recoverable: background agents,
yolo markers active, ultrado/RSI loops in progress, in-flight PRs awaiting
CI, concurrent claude sessions doing work.

For each: **what** is running (one line), **why** it matters (one line on
the future advantage to GRIP/HAL), **how** to check on it (one command or
file path).

### Bucket 2 — Open, needs closure (this session's debt)

PRs awaiting merge, hypotheses awaiting verification, branches awaiting
cleanup, worktrees pending teardown, partially-applied edits, untracked
drafts that should be filed or trashed, tasks marked in_progress that
have stalled.

For each: **what** is open (one line), **why** it was started (one line),
**how** to close it off — the concrete next action, with the 1-2-3 the
operator chooses from in the wizard.

### Bucket 3 — Backlog (waiting, not yours-this-second)

Pending tasks, open issues with `sprint-loop` label, deferred items in
session-context, blocked items (e.g. /evo on usage limit).

For each: **what** the work is (one line), **why** V>> wanted it (one
line connecting to GRIP/HAL future), **delegatable?** (yes/no) — items
flagged delegatable feed into the `/delegate` chain to the CodeTonight
team if V>> chooses.

## Output shape constraints (V>>'s voice)

These are not stylistic suggestions — they are the contract.

| Do | Do not |
|----|--------|
| British English; rhythm over rush | American English; manufactured urgency |
| Plain language, no metric codes (DELIGHT, KONO, etc.) translated through reasoning per Rule 17 | Internal jargon thrown raw at the operator |
| One-line per fact; concise | Bullet-list explosions; hedged phrasing |
| Direct ("merge it" / "delete it" / "move on") | "I think perhaps we might want to consider..." |
| Honour irony if V>> framed it ironically | Apply earnest analysis to playful register |
| Warmth where authentic (rare; only if context warrants) | Manufactured warmth ("Great question!", "Of course!") |
| Acknowledge the human is the decider | Pretend the agent has authority it doesn't |

V>>'s watchwords apply ad infinitum: *FACTA, NON VERBA*. The output
describes deeds, not intentions.

## Why this exists (the future advantage)

GRIP and HAL are large enough that no single session keeps the full state
in working memory. Auto-memory captures *what happened*; the task list
captures *what was planned*; git captures *what shipped*; Slack captures
*who knows what*. None of those layers, alone, answer "what is happening
RIGHT NOW and what wants my attention?".

This skill is the union view. It runs cheaply, often, between focused
work blocks. The HITL wizard converts the snapshot into closed-off items
in one pass — no items rotting in the backlog because nobody decided.

The delegation hook means CodeTonight team members (especially on Windows
where some GRIP/HAL components misbehave) can be assigned items without
V>> having to manually parse the backlog. Future-hookable: when invoked
with `--delegate`, the wizard offers each delegatable item to the
appropriate team member based on their capability profile from the
delegating-team-work skill.

## Sources scanned

This is the union of every state-bearing surface in GRIP. The skill reads
all of them in parallel before talking to the operator.

| Source | What it tells us | Read via |
|--------|-----------------|----------|
| `TaskList` (current session) | What this session was tracking | TaskList tool |
| `rules/session-context.md` | Cross-session continuity, last achievement, deferred work | Read |
| `state/rsi/checkpoint.json` (if exists) | Active RSI loop state | Read |
| `state/ultrado/session-*.json` (if any) | Active ultrado loops | Glob + Read |
| `state/session/yolo-mode.json` (if exists) | Active yolo bypass markers | Read |
| `git status` | Uncommitted work, current branch, untracked drafts | Bash |
| `git log --oneline -10` | What just shipped to local | Bash |
| `gh pr list --state open` | Open PRs (this repo) | Bash + jq |
| `gh issue list --state open --label sprint-loop` | Backlog issues | Bash + jq |
| `state/queue/pending/` (if any) | Verify-queue tasks | Glob |
| `learning/hypotheses.jsonl` (recent N) | Active falsifiable predictions | Bash + tail |
| `pgrep -x claude \| wc -l` | Concurrent claude sessions | Bash |
| `state/grip-run/logs.txt` (last entries) | Recent grip-run activity | Bash + tail |
| `~/.claude/.claude/worktrees/` | Active worktrees | Bash |

The reads run in one parallel batch — Shape A fanout. None share substrate
with each other or with the operator's working tree.

## HITL wizard flow

Per `skills/asking-users/SKILL.md` and `skills/creating-wizards/SKILL.md`,
the wizard is structured as one AskUserQuestion call per decision-needing
item, NOT a single mega-question. Each question is:

```text
Q: <item title in plain language, ≤80 chars>
   why: <one sentence on the future advantage>
   what: <one sentence on what specifically gets done>
   how: <one sentence on the mechanism>

Options (1-2-3 style — keep it KISS):
  1. Close it off    — execute the close action now
  2. Hand off        — delegate to CodeTonight team (only if delegatable)
  3. Defer           — push to backlog, handle later
  Other (free text)  — operator types a custom direction
```

Multi-select is OFF for these — one decision at a time, in sequence,
because the actions can have order dependencies (e.g. "merge PR" must
happen before "delete branch" for the same branch).

Hypothesis confirm: when an item carries an active falsifiable hypothesis
(via `lib/hypothesis_engine.py`), the wizard surfaces the hypothesis text
and asks whether the operator wants to mark it confirmed/falsified now or
let the deadline-trigger handle it.

## Execution sequence

1. **Scan** all sources from the table above in one parallel batch
   (Shape A — they don't share substrate).

2. **Classify** each finding into Live / Open / Backlog.

3. **Plain-language render** — for each item, translate metric codes and
   GRIP jargon to British English per Rule 17. No raw "DELIGHT 0.77,
   genome fitness 0.38" — reframe to "session quality good, feature
   scorecard stable".

4. **Output one-shot snapshot** — print the three buckets in order,
   without asking anything yet. The operator reads the full picture in
   one go before any wizard prompt fires.

5. **Wizard pass over Open bucket** — for each item that needs an
   operator decision, run AskUserQuestion with the 1-2-3 options. Skip
   items that are obviously "let CI/auto-bot handle this".

6. **Execute confirmed actions** in dependency order:
   - merges before branch deletes
   - branch deletes before worktree teardowns
   - delegations before backlog reshuffles

7. **Final summary** — Rule 11 anti-drift template (Done / Remaining /
   Open / Next), tight, no fluff.

## Delegation hook (chains to delegating-team-work)

Items flagged `delegatable: true` get offered to the operator as a
"Hand off" option. When chosen, the wizard transitions to
`/delegate` (the delegating-team-work skill) which:

- Matches the item to a CodeTonight team member's capability profile
- Especially: items tagged `windows-sensitive` route to Toopie / Andre
  whose Windows machines can validate things V>>'s macOS cannot
- Creates the GitHub issue assignment + Slack DM in one shot

This is the skill-graph chain referenced in this skill's
`chains_to: delegating-team-work` frontmatter field.

## Anti-patterns

- Do NOT ask the operator anything before showing the full snapshot.
  V>> wants the picture first, decisions second.
- Do NOT use AI fluff openers ("Great!", "Of course!", "I'd love to...").
  V>>'s identity file flags this as authenticity violation per the
  authentic-writing skill.
- Do NOT manufacture urgency on backlog items. Rhythm over rush. If an
  item has been on the backlog for a week, it can wait one more pass.
- Do NOT raise zero-sum framing ("we have to choose between X and Y").
  V>>'s governing philosophy is non-zero-sum; surface paths where
  multiple items can progress in parallel via /sprint Shape A or via
  delegation.
- Do NOT generate references to V>>'s late father — flagged in identity.md
  as sacred. Only ever quote V>>'s own writing on the subject.
- Do NOT translate spiritual references into secular equivalents — keep
  them in original form per identity.md.

## Composition with /sprint and /ultrado

`/whoa-update-pls` is read-mostly with selective writes (the wizard's
chosen actions). It is NOT a full sprint and should NOT trigger a
worktree, yolo activation, or hypothesis pre-registration. It runs
inline in the operator's main session.

If the wizard discovers more than one item needing closure-via-edit,
the right escalation is to **chain into `/sprint`** with the chosen
items as the wave's tasks. The skill should suggest this when it sees
3+ Open items that all need code edits.

When invoked **inside** a running `/ultrado` or `/sprint` session, the
skill defers wizard prompts to the breathing pause between waves so it
doesn't interrupt converge cycles.

## Falsifier

This skill is wrong if, over four weeks of activated usage:

- The output exceeds ~120 lines on a typical session (verbosity creep —
  V>>'s register is terse, not exhaustive)
- The wizard fires more than ~5 questions per invocation (each prompt
  costs operator attention; over-prompting violates rhythm-over-rush)
- Items consistently land in the wrong bucket (Live/Open/Backlog
  classification heuristic is faulty)
- Operators report the snapshot tells them what they already knew rather
  than surfacing the surprise

Track via:
- Output line count per invocation (capped at 120 lines)
- Wizard prompt count per invocation (target ≤5)
- Operator override rate ("nope, this is actually Backlog not Open")
- Hypothesis-confirmation accuracy when wizard surfaces active hypotheses

## Tests

`tests/test_whoa_update_pls.py` covers the structural contract:

- Frontmatter parses with required fields (name, status, triggers,
  integrates, chains_to)
- Triggers contain `/whoa-update-pls` and `/ultrawhao`
- No other skill claims either trigger (collision check)
- Body documents the three buckets, the wizard flow, the anti-patterns,
  and the V>>-voice contract
- The slash-command files (`commands/whoa-update-pls.md` and
  `commands/ultrawhao.md`) both invoke the skill

Goodhart-resistant: assertions check parsed YAML and body content from
the actual SKILL.md file, not call counts.

## Origin

V>>'s session-end directive (2026-04-28, post Rule 20 ship): build a
"hold the fuck up, what is exactly going on right now?" command. The
register itself is V>>'s — irreverent, direct, decisions-oriented,
British English, no fluff. Composes with the HITL stack (asking-users,
creating-wizards, hitl) and the delegation chain (delegating-team-work).
Future-hookable: CodeTonight team members on Windows can pick up
delegatable backlog items via the delegation chain, especially items
tagged windows-sensitive.

Primary Author: V>>
