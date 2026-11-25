---
name: prune-tasks
description: Archive completed harness tasks to cap the SessionStart reminder injection. Captures every task body to state/tasks/archive.jsonl first, then deletes only the completed ones via TaskUpdate(status="deleted"). Live count drops to pending + in_progress. Recoverable from the archive at any time. Use when the reminder block is bloating context or when /audit-efficiency flags TaskList substrate.
---

# /prune-tasks — Bound the TaskList substrate

## What this does

The Claude Code harness injects the full `TaskList` into context on session
start (and on idle "task tools haven't been used recently"). With 200+
completed tasks accumulated this costs ~14k tokens per fire — burned
before the operator has typed anything.

This skill archives every visible task to a durable on-disk JSONL
(`state/tasks/archive.jsonl`), then deletes the completed ones via
`TaskUpdate(status="deleted")`. The live `TaskList` drops to just the
pending + in_progress rows. The audit trail of completed work is
preserved on disk — `TaskGet` no longer works for archived IDs, but
the subject + status + timestamp survive in the JSONL.

Mirror of HOLISTIC.md (bounded kernel + sealed ledger + transport-shim)
and operational-context-fold-guard (warn-on-bloat) patterns, applied to
the TaskList substrate. See `plans/tasklist-bloat-remediation-plan.md`
for full design rationale.

## When to invoke

- The reminder block exceeds ~50 task lines (`/audit-efficiency` flags it).
- `[TASK-FOLD WARN]` fires from `hooks/task-list-fold-guard.py`.
- Post-sprint cleanup: many waves shipped, completed entries accumulating.
- Periodic hygiene (weekly).

Safe to re-run; idempotent — re-archiving the same snapshot produces the
same `snapshot_sha16` (R2 idempotency per `lib/holistic_synthesiser.py`).

## Protocol

1. **Capture** the live reminder block (the harness will inject it on the
   next tool call, or call `TaskList()` to materialise it).

2. **Save** the reminder text to `state/tasks/_reminder_snapshot_<DATE>.txt`
   using the `Write` tool. This is the safe-haven artefact.

3. **Run** the pruner — emits archive + digest + deletion list:

   ```bash
   python3 $HOME/.claude/lib/task_pruner.py \
     --input $HOME/.claude/state/tasks/_reminder_snapshot_<DATE>.txt \
     --source reminder-snapshot-<DATE> \
     --print-deletion-list \
     > $HOME/.claude/state/tasks/_pending_deletion_<DATE>.txt
   ```

   The archive (`state/tasks/archive.jsonl`) is now safely on disk with
   every task body. The deletion list contains the IDs of completed
   tasks to remove from the live harness store.

4. **Delete** each ID via `TaskUpdate(status="deleted")`. Batch in groups
   of ~30 parallel tool calls for throughput. The pruner has already
   preserved the audit trail; this step only mutates the live view.

5. **Verify** via `TaskList` — live count should now equal the pre-existing
   pending + in_progress count (the pruner's `keep_count` in the digest).

## Recovery

If a deleted task ID is later needed, query the archive:

```bash
jq 'select(.task_id == "1647")' $HOME/.claude/state/tasks/archive.jsonl
```

The subject, status-at-archive timestamp, and snapshot provenance are all
preserved. `TaskGet 1647` will no longer return — but the body lives on.

## Falsification

Hypothesis H-TASK-FOLD-1 (registered separately): after this skill ships,
the next 10 session starts each inject ≤ 5,000 chars of TaskList
reminder, versus the current ~56,000. Falsified if any one fires above
5 k (then the harness reminder is not actually bounded by live count and
this whole approach needs revisiting; see plan §8 step 6).

## Why not block the reminder directly?

The reminder injection is harness-internal — GRIP cannot intercept it.
We can only reduce the volume of *what gets injected* by pruning what the
harness considers "live". The skill plus the fold-guard hook combine to
keep that pool bounded.

## Cross-references

- `lib/task_pruner.py` — the canonical analysis module (Goodhart-anchored)
- `tests/test_task_pruner.py` — sealed-ledger invariant tests
- `hooks/task-list-fold-guard.py` — PostToolUse WARN when count regrows
- `plans/tasklist-bloat-remediation-plan.md` — full design rationale
  (Gentner SMT mapping to HOLISTIC.md / OCF pattern + AGORA-style review)
