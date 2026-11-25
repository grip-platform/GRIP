# The Session Memory Problem Nobody Talks About

**How we wired GRIP's memory systems into a single reliable pipeline.**

---

## The Problem

AI coding agents have a dirty secret: they forget everything between sessions.

Claude Code has hooks. GRIP has 1,661 memories in a 4D spatial database. There's an instance lineage tracking 487 sessions across 5 branches. There's achievement timelines, session state, KONO permanent memories cross-validated across dozens of interactions.

None of it was showing up at session start.

The infrastructure existed. The wiring didn't. A user starting a new session would get a blank slate. The system that was supposed to remember everything displayed nothing.

---

## Why Hooks Don't Work (The Way You'd Expect)

Claude Code's `SessionStart` hook runs with `async: true`. This means stdout from the hook is unreliable -- it might arrive, it might not, it might arrive after the agent has already started processing. You cannot build user trust on "might."

The GRIP hook ecosystem had three separate memory injection paths:

1. **Hook stdout** -- unreliable with async execution
2. **MEMORY.md injection** -- writes to a project file, but Claude Code doesn't auto-load arbitrary markdown files
3. **Instance resurrection** -- loads lineage data, but only when certain conditions are met

Three paths, zero reliability. Classic distributed systems problem: multiple inconsistent channels are worse than one reliable channel.

---

## The Discovery: rules/*.md Is the Only Guaranteed Path

Claude Code auto-loads every file in `~/.claude/rules/` as system context at session start. This is mechanical, synchronous, and guaranteed. No race conditions. No async timing issues.

The solution was obvious once we stopped trying to make hooks do something they weren't designed for:

```
Session End → Write rules/session-context.md → Next Session Auto-Loads It
```

One file. One path. One source of truth.

---

## Architecture

```text
Session N (ending)                    Session N+1 (starting)
┌──────────────────────┐              ┌──────────────────────┐
│                      │              │                      │
│  Instance JSON       │              │  Claude Code Engine  │
│  (lineage, state)    │──┐           │                      │
│                      │  │           │  Auto-loads:         │
│  KONO Substrate      │  │  writes   │  rules/*.md          │
│  (1661 memories)     │──┼─────────► │    ↳ session-context │
│                      │  │           │                      │
│  Achievement         │  │           │  Agent sees:         │
│  Timeline            │──┘           │  - Last achievement  │
│                      │              │  - Key memories      │
│  Strategy Transfers  │──────────►   │  - Strategy transfer │
│  (cross-project)     │   filters    │  - Remaining work    │
│                      │   permanent  │  - Tree lineage      │
└──────────────────────┘              └──────────────────────┘
```

### What Gets Written (~300 tokens)

The rules file is deliberately compact. It contains:

- **Project path** -- compartmentalization (only used if working in the same project)
- **Generation summary** -- Gen N, branch, message count, last achievement
- **Tree lineage** -- siblings and fork points when branches exist
- **Recent achievements** -- last 6 generations of work
- **Key memories** -- top 3 KONO memories, tagged permanent/transient
- **Strategy transfers** -- permanent memories from OTHER projects (cross-validated patterns only)
- **Remaining work** -- what was left unfinished (from instance `next_up`)

### What Doesn't Get Written

- Full conversation history (too large)
- Transient session state (project-specific, stays in instance JSON)
- Unvalidated memories (only permanent or high-fitness memories surface)

---

## The Three Dedup Layers

KONO's hybrid search (vector embeddings + FTS5 + RRF fusion) has a side effect: spatial expansion can re-add memories that are already in the result set. This produced 8 identical "authenticating-with-claude" results -- the same memory surfacing through different retrieval paths.

Three layers fix this:

1. **Source-level ID dedup** (`kono_substrate.py`) -- before the final sort, deduplicate by memory ID. Prevents spatial expansion overlap with hybrid results.

2. **Content-prefix dedup** (`unified-memory-brief.py`) -- 80-character prefix comparison. Catches semantic duplicates that have different IDs but identical content.

3. **Achievement-overlap dedup** -- removes KONO memories that substantially overlap with the achievement timeline. If an achievement says "feat: add converge metrics," you don't need a KONO memory saying the same thing.

---

## Strategy Transfer: Cross-Project Learning Without Contamination

The user's concern was specific: sessions from different projects were bleeding into each other. If you work on Project A then switch to Project B, you don't want Project A's session state confusing the agent about what you're doing.

The solution separates two types of memory:

**Project-specific** (compartmentalized):
- Achievement timelines from this project only
- KONO memories filtered by `project_path`
- Instance state and `next_up` from this project's lineage

**Strategy transfers** (cross-project):
- Only permanent KONO memories (cross-validated 3+ times)
- Only from OTHER projects
- Labeled with source project name
- Represent proven patterns, not session state

A permanent memory has been validated across multiple sessions and contexts. It earned its permanence. That's the kind of learning worth transferring. A transient memory about "fix the auth bug in project X" stays in project X.

---

## The Lifecycle

```text
┌─────────────────────────────────────────────────────────────┐
│ Session End Hook (session-end.sh)                           │
│                                                             │
│  1. Serialize instance → grip/{id}.json                     │
│  2. Write rules file:                                       │
│     unified-memory-brief.py --write-rules $PROJECT          │
│     → Loads latest instance JSON                            │
│     → Extracts achievement timeline (deduped)               │
│     → Recalls KONO project memories (deduped)               │
│     → Recalls strategy transfers (cross-project permanent)  │
│     → Writes rules/session-context.md                       │
│                                                             │
│ Session Start Hook (startup/grip.sh)                        │
│                                                             │
│  1. Writes rules file (fallback if session-end missed it)   │
│  2. Outputs brief to stdout (best-effort, async)            │
│                                                             │
│ Claude Code Engine (automatic)                              │
│                                                             │
│  1. Loads rules/session-context.md (guaranteed)             │
│  2. Agent acknowledges context in first response            │
└─────────────────────────────────────────────────────────────┘
```

The session-end hook is the primary writer. The session-start hook is belt-and-suspenders -- if the previous session crashed without running its end hook, the start hook regenerates the rules file from the latest available instance data.

---

## What We Chose Not To Do

**Auto-memory-saver checkpoints**: The checkpoint hook (context-gate-hook.py) triggers at soft thresholds (~10k token intervals). Originally, these created JSON snapshot files on disk that were never recalled. The fix eliminated the file layer entirely: checkpoints now store directly in KONO as episodic memories, making them recallable via the same unified-memory-brief pipeline. The original bug was trivial: the transcript path wasn't being passed to auto-memory-saver (called with `session_id --force` but no `--transcript`), producing empty snapshots.

**MEMORY.md injection**: The `--inject-memory` flag writes a compact brief into the project's `memory/MEMORY.md`. But Claude Code doesn't auto-load this file, making it dead code in the hook pipeline. We removed it from the hook calls. The flag still exists for manual CLI use.

**Mid-session rules updates**: We considered having the checkpoint hook update the rules file during long sessions. But the rules file is for the NEXT session, not the current one. The current session already has its context. Adding mid-session writes would add complexity for zero benefit.

---

## The Numbers

| Metric | Before | After |
|--------|--------|-------|
| Memory displayed at session start | 0 items | 6 achievements + 3 memories + 3 transfers |
| KONO duplicate results | 8 identical | 0 duplicates (3-layer dedup) |
| Hook stdout reliability | ~60% (async) | 100% (rules file) |
| Cross-project contamination | Uncontrolled | Compartmentalized + strategy transfer |
| Token cost per injection | 0 (nothing shown) | ~300 tokens |
| Files modified | 0 | 6 (one converge run) |

---

## The Broader Lesson

Infrastructure doesn't matter if it isn't wired up. GRIP had 1,661 memories, 487 tracked sessions, a full tree lineage DAG with 5 branches, and a 4D spatial memory substrate. All of it invisible to the user.

The fix wasn't building more infrastructure. It was a 300-token text file written to the right directory.

The right abstraction isn't always the most sophisticated one. Sometimes it's `echo content > rules/session-context.md`.

---

*February 2026. Built with two converge runs (scores: 90.6/100, 91.0/100).*
