---
name: ultragoal
description: Arm goals of ANY size on the native /goal Stop hook by splitting condition from context — the goal lives in a durable brief file (no length limit, survives compaction and /clear) with an exogenous DONE-predicate per deliverable, while /goal receives a short derived pointer condition (<=900 chars, native cap 4000). Use for "/ultragoal", "goal too long for /goal", "arm a goal with full context", "goal that survives /clear", or when a goal needs falsifiable, probe-verified completion instead of self-report.
invocable_name: ultragoal
status: Active
version: 1.0.0
created: 2026-07-09
user_invocable: true
command: ultragoal
triggers:
  - /ultragoal
  - goal exceeds 4000 chars
  - arm a goal with full context
  - goal that survives /clear
  - falsifiable goal checklist
see_also:
  - ultraprompt
  - converge
  - dfi-fib
  - saving-state
---

# /ultragoal — arm any-size goals on the native /goal Stop hook

## The split (why this exists)

Claude Code's native `/goal` installs a session-scoped Stop hook: the session
cannot end its turn until the condition holds; it auto-clears when met;
`/goal clear` disarms early. The condition string is capped at **4000 chars**
— and it dies with the session's context. Operators hit the cap stuffing the
whole brief into the condition, and lose the goal's context to compaction.

`/ultragoal` splits the two concerns:

| Concern | Lives in | Limit | Survives /clear? |
|---|---|---|---|
| **Context** (goal, why, anchors, checklist) | `plans/ULTRAGOAL-<slug>-<date>.md` | none | YES (file + pointer task) |
| **Condition** (the Stop-hook string) | derived pointer, `<=900` chars by construction | native 4000 | n/a (re-derivable from the file) |

Second split, the anti-Goodhart core: **checkboxes are telemetry; probes are
the criterion.** The derived condition binds to `bin/grip-py lib/ultragoal.py
verify <brief>` **exiting 0** — every deliverable's DONE-predicate re-run as
a live subprocess. A session cannot tick its way to done: only `verify
--tick` writes `[x]`, from real exit codes (status = telemetry, referent =
anchor — the `task-reconcile` doctrine applied to the goal's own checklist).

Native reinforcement (CC 2.1.163): the `/goal` Stop hook can now return
`hookSpecificOutput.additionalContext`, so the pointer condition can re-inject the
brief's next-undone deliverable into the model on each Stop check AND keep the turn
going, instead of only blocking. `/ultragoal` uses this to hold the goal's live
context in front of the session across compaction — the durable brief file stays the
source of truth; additionalContext is the cheap in-turn reminder. The criterion is
unchanged: `verify` exit codes, never the injected reminder, decide done.

## The protocol (verb dispatch — exact match, zero ambiguity)

The argument is a **verb** only when it is exactly one of
`status · run · verify · clear`; anything else is goal prose. Bare
`/ultragoal` = status of the newest open brief, else 3-line usage.

### `/ultragoal <goal prose, any length>` — the ARM pipeline

1. **FOLD-CHECK** — `slugify(goal)` → `find_brief(slug)` (cross-date: the
   slug is the identity, the date is metadata). Existing brief → print its
   ARMED card + the identical one-liner, **write nothing** (Banach no-op,
   f∘f = f at the invocation level).
2. **CRYSTALLISE** — author the brief per the `/ultraprompt` five parts
   ((a) run-mode, (b) WHERE-WE-ARE probes, (d) HELD/GATES, (e) GUARDRAILS
   — by reference; same R0 gates, same 8KB link-don't-inline budget), plus
   the two machine sections this skill adds:
   - `## Deliverables` — one per line, strict grammar:
     `- [ ] <item> — DONE-predicate: <probe cmd>`. Every probe is an
     **exogenous anchor class**: PR merged (`gh pr view N --json state`),
     CI green, endpoint 200 (`curl -sf`), tests pass (`pytest -q`), file on
     origin (`git show origin/main:<path>`). Never `echo done` — the lint
     floor refuses vacuous probes at verify time.
   - `## Criterion (converge exit)` — the verify-exit-0 clause, with the
     derived condition embedded (`Armed condition: …`) so ARM is
     reproducible from the file alone.
   Write via the kernel: `bin/grip-py lib/ultragoal.py compose --goal "…"
   --deliverable "item :: probe" …`, then `TaskCreate` a pointer task
   `ULTRAGOAL(<slug>): <goal>` at the file path (survives `/clear`;
   `/ultraprompt` convention).
3. **DERIVE** — `bin/grip-py lib/ultragoal.py derive <path> --oneliner` →
   the short pointer condition. Refuses (loud ValueError) when the brief
   has no checklist: an unfalsifiable goal is never armed. `derive` prints
   both the tool and the brief by absolute path (never relative to the
   cwd), through `bin/grip-py`, with `CLAUDE_DIR` pinned to the install, so
   the pasted condition works from a worktree or any other project folder.
   A brief composed before 2026-11-16 still carries the old relative
   `python3` pointer in its `Armed condition:` line. For such a brief, run
   `derive` (or `status`) again and paste its output, not that line.
4. **ARM** — print the ARMED card, then the ready-to-paste `/goal <condition>`
   line as the **final bytes of the turn** (the agent cannot invoke slash
   commands — the operator pastes; copy target at the bottom). If a goal
   hook is already active this session, skip the paste and proceed under it.

### `/ultragoal run` — execution, a protocol-level converge instantiation

Named per `rules/loop-engineering.md` (the kernel takes callables; "do the
next item" is agent labour — so the AGENT instantiates the protocol and the
kernel owns the deterministic legs):

- **state** = the brief's Deliverables checklist
- **criterion** = `bin/grip-py lib/ultragoal.py verify <path>` exits 0
  (exogenous — live subprocess probes the agent cannot please)
- **improve** = execute the item `bin/grip-py lib/ultragoal.py next <path>`
  returns
- **max_depth** = dfi-fib ladder (1,1,2,3,5,8) per item: attempts 1–2 at
  depth-0 main thread; escalate one rung with a documented reason
  (`rules/dispatch-economics.md`); fib-exhausted → annotate blocked, move on

After each item: `verify --tick <path>` (probes tick the box, never the
agent's pen) + reprint the card. All-verified → the native `/goal`
auto-clears on its next evaluation — the loop's exit IS the hook's release.

### `/ultragoal status | verify | clear`

- `status <slug-or-path>` — deterministic card: progress bar, goal-identity
  hash (progress-invariant: ticking never changes it, editing the contract
  does), condition, escape line. Degrades gracefully on checklist-less
  briefs.
- `verify [--tick]` — the criterion oracle itself; the same command the
  armed condition names. Fail-closed: timeouts and errors are failures.
- `clear` — close out: all-done → complete the pointer task, keep the file
  (audit trail); not done → confirm, annotate ABANDONED + reason. Either
  way remind: `/goal clear` releases the native hook (the escape hatch is
  never hidden — `rules/gate-resolution-escape-hatch.md`).

## The kernel (deterministic floor — no LLM, no network)

`lib/ultragoal.py` — pure stdlib + `lib/converge.py`. Every function is a
projection; `fold_brief` executes ON the converge kernel
(`converge(state=text, criterion=fixed-point, improve=_fold_pass)`) so
`result.satisfied` is a **runtime Banach witness** — a non-contracting pass
raises `FoldError` in production, not just at selftest.

| Verb | Guarantee |
|---|---|
| `compose` | canonical brief, a fold fixed point; cross-date Banach no-op on an existing slug |
| `derive [--oneliner]` | pointer condition `<=900 < 4000` by construction; refuses unfalsifiable briefs |
| `verify [--tick]` | re-runs EVERY probe live; exit 0 iff all pass; only path that writes `[x]`; vacuous-probe lint floor |
| `next` | first undone deliverable (the improve target) |
| `status` | deterministic card from file bytes alone (works in any later session) |
| `fold` / `selftest` | canonical rewrite / loud f∘f = f proof |

Tests: `tests/test_ultragoal.py` — 34 Goodhart-anchored tests, each naming
the mutation it catches (self-scoring verify, checkbox-anchored condition,
date-duplicated briefs, non-contracting fold, vacuous probes, payload in the
condition).

## Anti-patterns

| Anti-pattern | Why it fails | Do instead |
|---|---|---|
| Stuffing the brief into `/goal` | 4000 cap + dies with context | Brief in the file; condition = derived pointer |
| `echo done` / `true` DONE-predicates | Goal clears on theatre | Anchor-class probes; lint refuses vacuous |
| Hand-ticking `[x]` boxes | Self-scoring — boxes are telemetry | Only `verify --tick` writes ticks |
| Re-authoring the same goal daily | Duplicate briefs, split progress | FOLD-CHECK reuses cross-date (slug = identity) |
| Vague conditions ("make it great") | Hook can never cleanly clear | Falsifiable checklist + exit-code criterion |
| Held decision as a checklist item | Launders an operator gate into auto-action | `## Held / gates` section, never a deliverable |

## Composition

- **`/ultraprompt`** — the brief IS an ultraprompt whose task list is
  compiled into a machine-checkable checklist + explicit converge exit; this
  skill adds ONLY the two machine sections and inherits the rest by
  reference. Routing: in-session lock on prose → native `/goal` alone;
  next-session handoff, no lock → `/ultraprompt`; long goal + session lock +
  exogenous checklist → `/ultragoal`.
- **`/converge` (`lib/converge.py`)** — one honest literal callsite (the
  fold witness) + the RUN protocol instantiation. No decorative kernel
  naming.
- **native `/goal`** — never replaced, always ARMED: `/ultragoal` is the
  compiler; `/goal` is the runtime hook.

## Falsification (of this skill)

DELETE this skill if, over ~5 uses: operators bypass ARM and hand-write
`/goal` conditions anyway (the compiler adds no value); OR the brief never
once prevents a cap-hit or a self-scored clear that raw `/goal` would have
suffered; OR authoring cost exceeds the goal-management it saves. Mis-tunes
short of the kill: probe-lint false-positives on legitimate probes → widen
the denylist boundary, keep the floor; a theatre-clear slips past the lint →
promote lint WARN→DENY and (at N>=2 instances) wire `verify` as a real
settings.json Stop hook per `rules/knowledge-maturation-functor.md`. Track:
cap-hits (target 0), theatre-clears (target 0), brief reuse across sessions.

## Related skills

- `ultraprompt` — the authoring template the brief inherits (parts a/b/d/e).
- `converge` — the kernel the fold executes on; the RUN loop's shape.
- `dfi-fib` — the per-item escalation ladder.
- `saving-state` / `ultraclear` — the handoff trio the brief composes with
  when a goal spans sessions.

## Changelog

| Version | Date | Changes |
|---------|------|---------|
| 1.0.0 | 2026-07-09 | Initial — forged under a live /goal hook by a 3-lens design panel + judge synthesis (ULTRACODE). Exit-code criterion (boxes = telemetry), cross-date Banach fold, converge-witnessed fold, probe lint floor, 34 Goodhart-anchored tests. |
