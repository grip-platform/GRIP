---
name: ultraprompt
description: Write a self-contained next-session continuation brief that transfers WORKING STATE (not narrative) so the next session reaches full context in one read. Use for "/ultraprompt", "write an ultraprompt", "continuation prompt for next session", "next-session brief", or "handoff prompt". Composes with /save (persist subsystems) and /ultraclear (partition + cheap clear) as the session-handoff trio.
invocable_name: ultraprompt
status: Active
version: 1.0.0
created: 2026-07-08
user_invocable: true
command: ultraprompt
triggers:
  - /ultraprompt
  - write an ultraprompt
  - continuation prompt for next session
  - next-session brief
  - handoff prompt
see_also:
  - saving-state
  - ultraclear
  - holistic-memory
---

# /ultraprompt — write the next session's continuation brief

## What an ultraprompt is

A self-contained **next-session continuation brief**. You write it while your
context is hot, and the next session pastes it (or a task points at it) as the
FIRST message. It transfers **working state, not narrative** — the cure for
cold-start. The test: the next session reaches full working context in **one
read**, with **zero re-derivation**. It does not recap what happened; it hands
over exactly what the next session needs to keep going: verified state anchors,
numbered tasks with their grounding, what is held vs autonomous, and the
session-specific lessons that would otherwise be lost.

It is the opposite of a summary. A summary compresses the past for a human
reader. An ultraprompt equips the future for an executor. Every claim in it is
**probe-able** — the reader trusts nothing without a verification handle.

Origin: V>> 2026-07-08, repeated ask (3rd–4th time). The repeated-ask signal IS
the crystallisation trigger the `/create` gate exists for — this codifies a
pattern proven N>=3 in-tree (exemplars: `drafts/cc-paper/ULTRAPROMPT-next-session.md`,
executed successfully the next day with every task shipping;
`plans/ULTRAPROMPT-gitcrypt-per-user-gpg-hardening.md`).

## The template (the load-bearing core — five parts, in order)

### (a) Header — how to run it + routing

One line: how the next session should launch (e.g. `/frontier /ultrathink`),
and the Pool-A/Pool-B routing note per `rules/pool-a-offload-routing.md` — route
LLM-heavy councils/deliberation through Pool-B (`grip-sovereign-council`,
`lib/broly_council.py`, `grip-council --roster open` — $0 weekly), keep the
interactive REPL (Pool-A) for the deep synthesis + edits it structurally cannot
offload. If the prior session hit a limit, say so and say what to do.

### (b) WHERE WE ARE — verify-canonical anchors

Every state claim carries its **verification handle** so the reader can probe
before trusting. Match the anchor to the claim shape (per
`rules/verify-canonical-not-claimed.md`):

| Claim shape | Anchor to carry |
|---|---|
| code landed | commit SHA + repo (`git show origin/main:<path>`) |
| PR state | PR number + state + `gh pr view <n> --json state` |
| council verdict | seal (`sha256:…`) + the re-verify command |
| hypothesis open | hypothesis id + deadline (`lib/hypothesis_engine.py status`) |
| file exists | path + the gitignore warning if under `drafts/`/`state/` |
| service live | endpoint + `curl -sf <url>/<route>` |

**Gitignore warning is mandatory** when work lives under a gitignored path:
`drafts/`, non-hidden `state/`. Plain `rg` SKIPS those — tell the reader to Read
files DIRECTLY, and that `rg` needs `--no-ignore-vcs` to reach them (per
`rules/bash-safety.md`). A brief that omits this sends the next session hunting
for files `rg` silently hid.

### (c) NUMBERED TASKS — grounding + done-criterion + depth

Each task carries three things:

1. **Grounding** — the `file:line` / seal / prior verdict the task builds on
   (so the executor does not re-derive the context).
2. **Done-criterion** — a binary, probe-able statement of "finished" (a passing
   test, a green check, a recompiled artifact with 0 warnings).
3. **Depth guidance** — main-thread depth-0 vs Pool-B council vs background
   agent, per `rules/dispatch-economics.md` (the ~3:1 useful-work:floor gate).

### (d) HELD / GATES — operator decision vs autonomous

State plainly what is the **operator's decision** (submit the grant, publish the
post, sign the contract, merge a bootstrap-authority PR) versus what the next
session may do **autonomously**. Never let an ultraprompt launder a held publish
into an autonomous task — a held decision written as task #3 is the failure this
section prevents. Cross-reference the HALT classes in
`rules/operational-context.md` "No self-imposed blockers".

### (e) GUARDRAILS TO CARRY — the session-specific lessons

The freshly-learned failure modes and standing corrections that would otherwise
die with this session's context. Link the feedback memory when one exists
(`/recall "…"`). These are the "don't repeat what I just learned the hard way"
notes — the highest-value, most-perishable content in the brief.

## Conventions

- **Where it lands**: `plans/ULTRAPROMPT-<topic>.md`, OR next to the work when
  the work has its own directory (e.g. `drafts/<project>/ULTRAPROMPT-next-session.md`).
- **ALWAYS register a harness task** — `TaskCreate` a task titled
  `NEXT SESSION (<run-mode>): <goal>` whose description points at the file path.
  This is the **durable pointer that survives `/clear`** (precedent: task #326).
  The file is the payload; the task is the finger pointing at it.
- **Size budget: <= ~8KB.** An ultraprompt that needs more should **link files,
  not inline them** (per the anti-patterns below). If you are pasting file
  contents into the brief, path them instead.

## Quality gates (R0 applied to the artifact)

Every ultraprompt is itself subject to R0 (`rules/r0-critical-thinking.md`):

- **Every factual claim carries evidence or a probe command.** No bare "the API
  is fixed" — either the commit SHA or the `curl` that proves it.
- **Scope qualifiers on anything unverified.** Mark it `UNVERIFIED — probe
  first` rather than asserting it. "I believe X landed" is not "X landed".
- **No bare internal numbers.** Gloss every PR/task/issue id: `the receipt
  verifier PR (#3884)`, never bare `#3884` (per `rules/canonical-output-style.md`
  Layer-3 gloss clause).
- **Negative/existence claims** need exhaustive scope or qualification (per
  `rules/prove-it-claims-canonical.md`): "no creds anywhere" needs the keychain +
  transport logs checked and named, not a 4-dir grep.

**Falsification of an individual ultraprompt**: it is WRONG if the next session,
following it, acts on a **stale claim that a listed probe would have caught**.
Therefore every claim MUST be probe-able — the probe is the antibody against the
brief going stale between when it was written and when it is read.

## Anti-patterns

| Anti-pattern | Why it fails | Do instead |
|---|---|---|
| Narrative recap ("we spent the session on…") | Transfers story, not state; reader still cold-starts | Transfer verified anchors + open tasks |
| Inlining file contents that can be pathed | Bloats past 8KB; goes stale vs the real file | Path the file + a one-line probe |
| Omitting the gitignore warning | Next session's `rg` silently hides `drafts/` files | Warn: Read directly / `rg --no-ignore-vcs` |
| Mixing held decisions into autonomous tasks | Launders an operator gate into auto-action | Separate HELD / GATES section (part d) |
| Bare internal numbers (`fix #3884`) | Reader cannot tell what it is | Gloss every id |
| Claims with no probe | Reader must trust or re-derive | Attach SHA / command / seal to each claim |
| **Writing it AFTER `/clear`** | Context is already gone — too late | Write it while context is HOT, before the trio's clear step |

## Composition — the session-handoff trio

Run in this order at end-of-session:

1. **`/ultraprompt`** (this skill) — write the brief while context is hot; register the pointer task.
2. **`/save`** (`saving-state`) — persist every memory subsystem (KONO, instance JSON, MEMORY.md).
3. **`/ultraclear`** — partition HOT/COLD/FOLDED, cheap-serialise the checkpoint, then you run the native `/clear`.

`/ultraprompt` captures the *forward intent* (what to do next); `/save` captures
the *durable memory* (what is known); `/ultraclear` does the *cheap lossless
clear* (freeing the window). All three, in order, is a clean handoff. See
`skills/ultraclear/SKILL.md` and `saving-state`.

## Falsification (of this skill)

Wrong if, over ~5 uses: the next session still **cold-starts** — re-derives state
the brief already contained (the brief is not transferring working state → fix
the template, likely the WHERE-WE-ARE anchors); OR the brief's **probes are never
used** (theatre — the anchors are decoration, not load-bearing → drop them); OR
**writing it costs more than the cold-start it prevents** (the brief is
over-produced → tighten to the five parts, enforce the 8KB link-don't-inline
budget). Track: next-session re-derivation events (target 0); probe-command usage
rate; ultraprompt authoring time vs cold-start time saved.

## Related skills

- `saving-state` (`/save`) — the durable-persist half of the trio.
- `ultraclear` — the cheap-lossless-clear half of the trio.
- `holistic-memory` — the long-term kernel a FOLDED brief defers to.
- `preplan` — lighter "prepare a plan for next session"; `/ultraprompt` is the
  full working-state handoff with verify-canonical anchors.

## Changelog

| Version | Date | Changes |
|---------|------|---------|
| 1.0.0 | 2026-07-08 | Initial — five-part template, R0 quality gates, handoff-trio composition. Origin V>> 3rd–4th repeated ask; N>=3 in-tree exemplars. |
