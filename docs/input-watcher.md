# INPUT watcher — spec

**Goal.** A drop folder, `PARAMOUNT_BACKUPS/INPUT/`, that is both an *inputs backup*
and an *ingestion point*: the moment a file lands there, GRIP absorbs it into the
right subsystem — no operator in the loop.

**Why both halves matter.** The outputs of a session get backed up (contracts, PDFs,
handoff docs). The inputs — the things you dropped in to be understood — currently
vanish. INPUT makes the inputs first-class: they are copied into the backup root by
virtue of living there, *and* they are ingested so the knowledge survives the file.

## Routing — by what the thing is

| Input | Class | Action |
|---|---|---|
| A git repository (a directory with `.git`, or a `.bundle`) | `grip-anywhere` | queue a ticket — analyse the repo's health, surface P0/P1, RSI plan (agent workflow, not a syscall) |
| A text file (`.md` `.txt` `.json` `.csv` `.yaml` …) | `absorb-knowledge` | call `lib.hal.knowledge_absorption.absorb_content` directly (deterministic syscall) |
| A directory that is not a git repo | `absorb-graph` | queue a ticket — build the cross-entity graph (agent workflow) |
| Anything else (pdf, image, binary) | backup-only | no absorb; it is already saved by being in INPUT |

## The honest boundary (why it is split this way)

`absorb-knowledge` is a **syscall** — a watcher can call it and get a deterministic
`{absorbed, filename}`. `absorb-graph` and `grip-anywhere` are **skills** — they need
an agent (a `/broly` mesh). A launchd watcher cannot run an agent, and it must not
pretend it did. So:

- The watcher does the mechanical half: classify, absorb what is a syscall, and
  **queue a ticket** for what is a skill.
- A GRIP session (the `/broly` mesh) consumes the queue — `INPUT/queue.jsonl` — and
  dispatches `grip-anywhere` / `absorb-graph` on the ticketed paths.

That split is the load-bearing honesty: a ticket is not a claim that the graph was
built. It is a record that the work is owed.

## Lifecycle (idempotent, lossless)

1. `WatchPaths` fires the watcher on a change in `INPUT/`.
2. The watcher scans `INPUT/` for entries not yet in the `processed/` log.
3. Each entry is classified, acted on (syscall) or ticketed (skill), then **moved**
   to `INPUT/processed/` and recorded in `processed.log` — moved, never deleted, so
   nothing is silently lost and re-running absorbs nothing twice.

## Security — git-tracked, never leaking

INPUT lives in `PARAMOUNT_BACKUPS`, which is deliberately **outside every repo**. To
be "git-tracked securely" it gets its **own** repository with git-crypt, so the
ingested inputs are versioned *and* encrypted — not committed into the public-bound
GRIP tree. The watcher refuses to run if `INPUT/` is inside a git repo of its own
(the same guard `lib/paramount_backups` already enforces for the root).

## Files

- `bin/grip-input-watch` — the watcher (classify → absorb/queue → move/log).
- `bin/grip-input-watch.plist` — the launchd agent (`WatchPaths` on `INPUT/`).
- `lib/input_watch.py` — the pure classify/route/move logic (testable), thin CLI on top.
