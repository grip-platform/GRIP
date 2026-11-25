---
name: remember
description: Durably remember a rule, fact, or lesson FOREVER — write it to the tier that is re-loaded every session (rules/ + CLAUDE.md for PARAMOUNT, rules/ for a doctrine, memory/ + MEMORY.md for a lesson) so it can never be silently forgotten. Use when the operator says "/remember", "remember this forever", "never forget X", "make this a rule", or hands you a durable rule/preference/lesson to encode. Themed on Mufasa's "Remember who you are." Thin orchestrator over lib/remember.py + git commit.
---

# /remember — "Remember who you are."

> Mufasa: *"You have forgotten who you are and so have forgotten me... Remember
> who you are."*

A one-command way to encode a rule/fact/lesson so **GRIP never forgets it** —
because it is written where it is read at the moment it matters: a memory record
in the index every session loads, a rule on the channel that reaches the readers
who need it.

## When to use

- `/remember <rule or lesson>` — the operator wants something remembered forever.
- "never forget…", "make this a rule", "this is a PARAMOUNT rule", "commit this
  to memory forever".

## The three durability tiers (pick by importance)

| Tier | Written to | Loaded when | Use for |
|------|-----------|-------------|---------|
| **paramount** | `rules/<slug>.md` **+** one line in `CLAUDE.md` under "Remembered laws" | the line every session; the rule by its channel | non-overridable doctrine |
| **rule** | `rules/<slug>.md` | by its channel | a standing doctrine |
| **memory** | `memory/<slug>.md` **+** a pointer line in `MEMORY.md` | every session (index) | a lesson, preference, or fact |

## A rule's channel: who reads it, and when

Every rule names its channel (KERN v2). The backend refuses a rule without one,
lists it in `config/rules-scope.json` to match, and keeps `settings.json`
`claudeMdExcludes` in step, so a rule can no longer merge unclassified.

| Channel | Who receives it | Use for |
|---------|-----------------|---------|
| `floor` | every session and every subagent, at startup | claim integrity and irreversible harm only: the floor is small on purpose |
| `main` | the main session only, as one line of its SessionStart index until the main-session tier lands | operator-facing rules and general working rules |
| `file` | whoever reads or writes a file matching `--paths` | a rule about a kind of file (tests, Python, workflows) |

The task-brief and command channels arrive with their hooks in phase 2 of the
KERN v2 plan. A rule pinned in `lib/rules_scope.MUST_BE_SAFETY_CORE` stays on
`floor` until then.

Every rule also names its **readers**, who it is for (`--readers`, repeatable):
`makes-claims` (every agent), `writes-code`, `runs-shell`, `writes-repo`,
`sends-outside`, `dispatches`, `handles-personal-data`, or the main session's own
`talks-to-operator` and `orchestrates`. The channel says where the rule is
delivered. The readers say who needs it. A `main` rule whose readers include a
subagent capability reaches no subagent until the task brief lands, and the
backend says so. A rule listed in `lib/rules_scope.MUST_REACH` must keep the
readers pinned there.

## Procedure (thin — reimplement nothing)

1. **Classify** the content into a tier (ask via `/asking-users` only if genuinely
   ambiguous; default: a durable *rule/preference the operator states* →
   **paramount**; a discovered lesson → **memory**).
2. **Ask for the channel and the readers** of a rule via `/asking-users`: one
   option per channel from the table, each with what it costs and who it
   reaches, your recommendation first, then which readers need it. Never default
   a rule onto `floor` to be safe: every floor character is paid by every session
   and every subagent.
3. **Draft two texts.** The **operative text** goes in the rule: each clause one
   command, condition first, then one "because" sentence. The **narrative** goes
   to the annex: origin, dates, incidents, measurements, falsification history.
   For a memory, follow the frontmatter memory format.
4. **Write it deterministically** via the backend (never hand-wire the files):

   ```bash
   # a rule: tier paramount or rule
   grip-py lib/remember.py --tier paramount \
     --slug <kebab-slug> --title "<Title>" \
     --desc "<one sentence, at most 200 characters>" --body-file <operative.md> \
     --channel <floor|main|file> [--paths "<glob>"]... \
     --readers <reader> [--readers <reader>]... \
     --narrative-file <narrative.md> \
     --repo "$PWD"        # worktree root for rules/ + CLAUDE.md

   # a memory record: no routing flags or readers (the backend refuses them)
   grip-py lib/remember.py --tier memory \
     --slug <kebab-slug> --title "<Title>" --desc "<one line>" --body-file <body.md> \
     --kind user|feedback|project|reference [--memory-dir <dir>]
   ```

   The backend is **idempotent** — re-remembering a slug overwrites its files,
   moves it if its channel changed, and never double-appends a pointer.
5. **Report the size line** the backend prints: how the always-loaded total
   changed, and whether it is now over the ratchet in `config/rules-scope.json`.
6. **Commit** the change (atomic, one logical rule = one commit; push; PR per
   `CLAUDE.md` Commit-Often): the rule, its annex, `config/rules-scope.json`,
   and `settings.json` and `CLAUDE.md` when they changed. The memory tier's
   `memory/` writes are the live, session-inherited copy and need no PR.
7. **Confirm** with the closing lines the backend prints. They say where the
   thing is now read, by channel: never promise "every session" for a `main` or
   `file` rule, because that is not where it loads.

## Falsification

`/remember` is working iff a thing remembered at session N reaches its readers in
session N+1 where its channel says: a `floor` rule in every session's and
subagent's startup load, a `main` rule as a line of the main session's index, a
`file` rule when a matching file is read, a memory record in `MEMORY.md`. If it is
absent where its channel promises it, the routing is broken; if it is present but
too late to matter, the channel choice was wrong. The backend write itself is
deterministic and tested.

## Related

`lib/remember.py` (backend) · `commands/remember.md` (slash entry) ·
`skills/holistic-memory` + `skills/saving-state` (the memory substrate it writes) ·
`rules/knowledge-maturation-functor.md` (when a lesson earns rule status) ·
`skills/creating-skills` (this skill was built to its standard) ·
`skills/creating-wizards` + `skills/hitl` (siblings of the step-1 `/asking-users`).
