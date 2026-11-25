---
name: verify-queue
description: Verify-gated markdown task queue. Drop tasks into state/queue/pending/, runner moves through active/, deletes on verify pass or moves to failed/ on verify fail. Hypothesis-engine integrated -- every verify outcome registers as CONFIRMED or REFUTED.
status: Active
effort: medium
priority: standard
version: 1.0.0
created: 2026-04-26
triggers:
  - queue task
  - verify-gated
  - drop a task
  - tick the queue
  - failed task inspection
integrates:
  - hypothesis-driven-development
see_also:
  - learn
  - efficiency-rules.md (Rule 19)
---

# Verify-Gated Task Queue

A markdown-task queue that catches mistakes before they ship. Each task carries
a `verify: |` block in its YAML frontmatter; the runner only deletes a task
after that block exits 0. Failing tasks are preserved in `failed/` with their
log for human inspection. **No auto-retry, ever.**

## Why this exists

Auto-retry turns verify gates into theatre. The queue exists to catch the
mistakes you *want* to learn from, not paper over them. When a verify fails:

1. The task moves to `failed/<id>.md` with a sibling `<id>.log`
2. Status is registered as REFUTED in the hypothesis engine
3. A human (you, or a later session) inspects the log
4. They fix either the artefact OR the verify, then `/queue:retry <id>`

This is discipline. It doubles as a hypothesis-driven-development feed:
each verify is a falsifiable prediction, and the pass/fail outcome flows
into genome fitness via `lib/hypothesis_engine.py`.

## Layout

```
state/queue/
├── .gitignore           ← ignores task files, keeps .gitkeep
├── TEMPLATE.md          ← copy this when authoring a new task
├── pending/             ← drop tasks here
├── active/              ← transient: task being processed
└── failed/              ← preserved + .log sibling for inspection
```

## Slash commands

| Command | Action |
|---|---|
| `/queue:add <title>` | Stamp a new task from TEMPLATE into pending/. Leave verify+body placeholders for human filling. |
| `/queue:list` | Show pending/active/failed counts + titles. |
| `/queue:tick` | Process ONE pending task: move active → do work → run verify → delete or fail. |
| `/queue:run [N]` | Drain up to N tasks via tick. Stops on first failure. |
| `/queue:retry <id-or-glob>` | Move matched failed/ entries back to pending/, drop stale .log. |
| `/queue:gc [days]` | GC failed/ entries older than N days (default 7). Confirms before delete. |

## Library API

```python
from lib.verify_runner import run_task

rc = run_task("state/queue/pending/2026-04-26-foo.md",
              log_path="state/queue/active/2026-04-26-foo.log")
# rc == 0 → verify passed, hypothesis CONFIRMED
# rc != 0 → verify failed, hypothesis REFUTED, .log captured
# rc == 3 → no `verify: |` block in the task file
```

## Verify block conventions (learned the hard way)

These come straight from `drafts/claude-code-debunked.md` section 7 — they
are author-time foot-guns the queue runner cannot rescue you from:

- **`set -e` at the top.** Multi-line verify silently passes the first check
  and ignores subsequent failures otherwise.
- **`grep -qF` for literal `$`.** Without `-F`, `$` is interpreted as
  end-of-line and the verify fails silently while the artefact is correct.
- **Per-line grep blindness.** A regex like `grep -qE "find .*failed.*-mtime"`
  won't match if your artefact splits the command across lines with `\`.
  Either collapse to one line, or split the check into multiple greps.
- **Non-default ports.** Stale processes on 3000 break verify-spawned servers.
  Use `PORT=399X` per task.
- **`trap 'kill $pid; wait' EXIT`** after spawning a server in a verify
  block. A crashed verify must not leave a zombie holding a port.

## Hypothesis-engine integration

Every `run_task` call registers a hypothesis with:
- `claim` = task title
- `metric` = `"verify_pass"`
- `prediction` = `"pass within session"` (rc==0) or `"fail within session"` (rc!=0)
- `actual_value` = 1.0 (CONFIRMED) or 0.0 (REFUTED)
- `notes` = last 20 lines of combined stdout+stderr (REFUTED only)

This means the queue's pass/fail rate is observable via
`lib/hypothesis_engine.py status` over time. Confirmation rate below 30%
means tasks are too ambitious for their verify blocks; above 70% means
verify blocks are too easy. The Goodhart band (30-70%) is the target.

## The "don't auto-retry" rule (PARAMOUNT)

When a task's verify fails, it moves to `failed/` and stays there. Do NOT
re-edit and re-run inside the same tick. The next human inspects the log,
decides what's wrong, fixes either the artefact or the verify, and uses
`/queue:retry` explicitly. This is enforced socially, not mechanically —
the queue itself contains no retry loop.

## Falsification of this skill

This skill claims: "Every verify failure is more informative than the
underlying problem because it surfaces a specific testable prediction
that was wrong."

Falsified by: A 30-day audit shows >50% of REFUTED hypotheses had verify
blocks that asserted the wrong thing (false negatives where the artefact
was correct). At that rate, the queue is producing more noise than signal
and the conventions list above needs revision.
