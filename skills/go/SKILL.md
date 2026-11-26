---
name: go
description: >
  InnerspeakerGo. The stripped-down single-shot sibling of /ultrado. Pick the
  current remaining work, reason briefly via inner speech, execute with zero
  ceremony, report tersely. One deliverable per invocation. No Fibonacci waves,
  no hypothesis pre-registration, no broly councils, no plan files. If you need
  any of those, use /ultrado instead. Yolo mode with a 1-hour TTL keeps halting
  gates relaxed; quality gates still fire.
status: Active
effort: low
priority: high
version: 1.0.0
created: 2026-04-19
triggers:
  - /go
  - "just go"
  - "go for it"
  - "innerspeaker go"
integrates:
  - yolo_mode.py
  - session_isolation.py
  - hypothesis_engine.py
see_also:
  - ultrado
  - converge
  - save-exit
  - asking-users
  - creating-wizards
  - hitl
---

# /go — InnerspeakerGo

The lightweight sibling of `/ultrado`. Single-shot, one deliverable, report, exit.

**Plain English summary**: pick the next thing on the list, think
briefly about what to do, do it, ship it, tell the operator what
happened, exit. No planning files, no councils, no recursive loops.
One deliverable per invocation — if the task has multiple parts, /go
aborts and tells the operator to use /ultrado instead. 1-hour ceiling
on the halting-gate bypass.

**Glossary**:
- Yolo mode = halting-gate bypass; quality checks still fire
- Worktree = isolated working copy of the repository
- Inner speech = brief chain-of-thought reasoning written as a paragraph
- Anti-drift output = the four-line "what's done / what's left" summary

## Intent (read this once, never confuse the two)

| Command | Intent | Mechanism | TTL |
|---------|--------|-----------|-----|
| `/go` | "just DO it" — pick the next thing, ship it, exit | Single-shot, no loop | 1h |
| `/ultrado` | "do everything" — recursive falsification-driven convergence | Fibonacci waves, hypotheses, councils | 4h |

`/go` is not `/ultrado --waves 1`. The difference is cognitive framing, not config.
Operators reach for `/go` when they want motion. They reach for `/ultrado` when
they want rigour. Different name, different intent.

## Invariants (non-negotiable)

1. **SINGLE-SHOT.** No wave loop. Phase 1 through 5 run exactly once. If the work reveals more deliverables, report them in the anti-drift output and exit. The operator decides whether to re-invoke.
2. **ONE DELIVERABLE per invocation.** If the task decomposes into multiple shippable deliverables, abort after Phase 2 and ask the operator, once, whether to switch to `/ultrado` -- a genuine fork (a multi-wave loop instead of one shot), asked as a question with your recommendation per `skills/hitl/SKILL.md`. On a yes, start `/ultrado` yourself; never hand over the command to type.
3. **NO AUTO-SAVE.** Commits are durable via git. `/save` runs on session boundaries, not after every `/go`. Phase 4 prints a reminder if commits landed.
4. **NO CEREMONY.** No plan file. No hypothesis pre-registration. No broly spawning. No `/ultraplan`. No test generation unless the task literally is "add a test". Chain-of-thought reasoning only.
5. **NO FORCE PUSH, EVER.** Yolo mode never bypasses remote history rewrites. That requires `GRIP_AUTONOMOUS=<key>:2` with stored secret + consent phrase.

## The Loop

### Phase 0: PRE-FLIGHT — WORKTREE ISOLATION (MANDATORY)

Same protocol as `/ultrado` Phase 0.0. `/go` is an ultrado-class autonomous
loop for Gate O purposes, so the `context="ultrado"` argument is correct.

Run as a single Bash call before any other tool:

```bash
mkdir -p ~/.claude/state/go
python3 -c "
import json, os, time
marker = os.path.expanduser(f'~/.claude/state/go/session-{os.getpid()}.json')
open(marker, 'w').write(json.dumps({
    'timestamp': time.strftime('%Y-%m-%dT%H:%M:%S%z'),
    'context': 'go', 'pid': os.getpid(), 'cwd': os.getcwd(),
}))
print(f'go marker: {marker}')
" && PYTHONPATH=$HOME/.claude $HOME/.claude/venv/bin/python -c "
from lib.gates.session_isolation import require_worktree_isolation
msg = require_worktree_isolation('ultrado')
if msg:
    print(msg)
    import sys; sys.exit(2)
print('[go pre-flight] worktree isolation OK')
"
```

| Exit | Meaning | Action |
|------|---------|--------|
| 0 | Clear | Continue to Phase 0.1 |
| 2 | PARAMOUNT block | HALT. Print returned message. Spawn worktree via `grip-worktree.sh add` and re-invoke `/go` inside it. |
| other | Env error | Report and ask operator |

### Phase 0.1: ACTIVATE YOLO (MANDATORY, 1-hour TTL)

```bash
PYTHONPATH=$HOME/.claude/lib $HOME/.claude/venv/bin/python -c "
from gates.yolo_mode import activate_yolo
activate_yolo('/go', ttl_seconds=3600, reason='${GOAL:-go session}')
print('yolo active (1h TTL)')
"
```

The 1-hour ceiling is deliberate: `/go` sessions are short. If the TTL fires
mid-work, that's the signal that the task needed `/ultrado`, not `/go`.

Deactivation is MANDATORY at Phase 5 end. If `/go` crashes, the TTL
auto-invalidates the marker at next gate check.

### Phase 1: PICK (goal selection)

In priority order:

1. Operator provided a goal on the command line → use it
2. Operator gave no goal → read instance JSON `next_up`, use the first item
3. No `next_up` → read `rules/session-context.md` remaining work
4. No remaining work → ask operator via `AskUserQuestion`

No `/recall`. No `/ultraplan`. No plan file. One line mental summary.

**Time budget: < 20 seconds. Tier 0-2 retrieval only.**

### Phase 2: INNER SPEECH (the speakable paragraph)

Write one paragraph (≤ 4 sentences) of self-dialogue, plain language, no
jargon. It must answer three questions:

1. What is the smallest shippable deliverable for this goal?
2. What would break if I did that?
3. What is the ONE tool call that starts forward motion?

**Multi-deliverable abort clause**: if the answer to (1) is "multiple things"
or if (2) lists three or more distinct breakage modes, STOP and tell the
operator:

> "This task has N deliverables. /go does one at a time. Run /ultrado instead,
> or narrow the goal to a single deliverable and re-invoke /go."

Then deactivate yolo and exit cleanly. No harm done.

**No broly. No `/ultrathink`. No ToT branching.** Chain-of-thought only.

### Phase 3: EXECUTE (direct implementation)

Use `Edit` / `Write` / `Bash` directly on the shippable deliverable. No
temp scripts (Rule 4). No speculative abstractions (YAGNI).

If the deliverable is code and the existing tests cover the change, run
them. If it's docs or config, no tests are generated. Tests only when the
task IS "add a test" or a regression requires one.

At end of Phase 3, if files changed:

```bash
git add <specific files> && \
git commit -m "type: terse description

Primary Author: <operator signature>" && \
git push
```

Fill `<operator signature>` with what `bin/grip-operator-signature` prints.

If the branch is a feature branch, create the PR (`gh pr create`). If CI
passes and admin-merge is allowed, merge. If protected branch, HALT and
ask via `AskUserQuestion` (Rule 7).

### Phase 4: REPORT (anti-drift, plain language)

Emit the tight 4-line template (Rule 11, Rule 17):

```
Done: <plain English — skip if commit message already says it>
Remaining: <items revealed during Phase 3 that did NOT ship>
Open: <PRs, gaps, merges in flight>
Next: <one concrete step for the operator or the next /go>
```

When the commit subject already says what landed, drop the "Done"
line — three lines is fine. "Remaining/Open/Next" are always-on.

If commits landed this run, append ONE extra line:

```
Reminder: run /save when done batching (commits are durable, context is not).
```

That's the only post-execution ceremony. No summary. No achievement banner.

### Phase 5: DEACTIVATE YOLO (MANDATORY)

```bash
PYTHONPATH=$HOME/.claude/lib $HOME/.claude/venv/bin/python -c "
from gates.yolo_mode import deactivate_yolo
deactivate_yolo('/go')
"
```

Clean up the pre-flight marker file as a courtesy — not load-bearing, but
keeps `state/go/` from accumulating stale PIDs.

```bash
rm -f ~/.claude/state/go/session-$$.json 2>/dev/null || true
```

Then exit. No `/save`. No extra tool calls.

## Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| goal | auto-pick from next_up | What to ship (one deliverable only) |
| --dry-run | false | Skip Phase 3 commit/push (analyse, don't ship) |

No `--depth`, no `--waves`, no `--precision`. If you need those flags, you
need `/ultrado`.

## Token Budget Guidance

Soft target: ≤ 8k tokens per invocation (reads + edits + commit + report).
Not enforced as a hard cap — aborting mid-edit is worse than running over.
If your run routinely exceeds 8k, the tasks are too big for `/go`. Switch
to `/ultrado`.

## Falsification Criteria

This skill is WRONG if, over 3 months of telemetry:

- **H-GO-1**: < 60% of `/go` invocations that were supposed to produce file
  changes actually produced a committed file change within 15 minutes wall-clock
  (over N ≥ 20 invocations). Denominator excludes analysis-only invocations
  where no commit was intended.
- **H-GO-2**: > 40% of `/go` invocations fire the context gate (85%) before
  completing Phase 4. If this happens, `/go` isn't small enough to be worth
  the separate entry point.
- **Usage floor**: < 5 invocations over 30 days means operators aren't
  reaching for `/go` — they're still defaulting to `/ultrado` or direct
  commands. The command is not serving its cognitive-framing purpose.

If any of these falsify, delete the skill and revert to `/ultrado --waves 1`
with a `GRIP_GO_ALIAS` preset.

Both hypotheses are registered: **H158** (go_commit_rate ≥ 60%) and
**H159** (go_context_gate_rate ≤ 40%), both deadline 2026-07-18.

## Relationship to Other Commands

```
/go
|-- Phase 0   : session_isolation.require_worktree_isolation (shared with /ultrado)
|-- Phase 0.1 : yolo_mode.activate_yolo (shared; different TTL)
|-- Phase 1   : direct read of next_up / session-context (no /recall)
|-- Phase 2   : inline chain-of-thought (no /ultrathink, no broly)
|-- Phase 3   : direct Edit/Write/Bash + commit + push (no /auto-ship)
|-- Phase 4   : anti-drift output (Rule 11)
+-- Phase 5   : yolo_mode.deactivate_yolo
```

`/go` shares safety infrastructure with `/ultrado` but uses none of the
adaptive-precision, hypothesis-driven, or multi-agent machinery. When in
doubt between `/go` and `/ultrado`: if the work is ONE thing you can
describe in one sentence, use `/go`. Otherwise `/ultrado`.

## Anti-Patterns

- Do NOT run `/broly` inside `/go`. If you need a council, use `/ultrado`.
- Do NOT register hypotheses inside `/go`. The SKILL's own hypotheses (H-GO-1, H-GO-2) track its aggregate behaviour.
- Do NOT write plan files. If you need a plan, use `/ultraplan`.
- Do NOT loop. Single-shot. Exit when Phase 4 prints.
- Do NOT auto-save. Operator runs `/save` on session boundaries.
- Do NOT generate tests speculatively. Tests only when the task IS tests or regression demands them.
- Do NOT skip the worktree pre-flight. The git-crypt + parallel-session failure mode is real.
- Do NOT force push under any yolo-mode TTL. Never.

## Why This Exists

V>> said "just go" many times across sessions. The phrase has a shape:
pick the thing, reason briefly, do it, tell me what happened. That shape
is smaller than `/ultrado` and different from `/converge` (which is a
score-based loop). `/go` is that shape codified so future sessions pick
the right precision instead of reaching for the heavyweight command for
every small task.

Born from V>>'s "just go" pattern observed across sessions — pick
the thing, reason briefly, ship it, tell me what happened. `/go` is
that shape codified.
