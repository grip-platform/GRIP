# GRIP Git-Lock Architecture

**Status**: Active as of Gen 509 (2026-04-11).
**Origin**: V>>'s observation during a failed /closeout: "git/index.lock is
a really poor architectural design. Improve it." — a transient collision
with a concurrent Claude session degraded a closeout to memory-save-only.
**Hypothesis**: [H022](../learning/hypotheses.jsonl) registered 2026-04-11.

## The Problem With Git's Native Lock

Git uses `.git/index.lock` as a single global mutex across the entire
index. It has six structural weaknesses:

1. **Single global mutex** — no granularity. Any `git add/commit/reset/
   checkout/merge` contends on the same file.
2. **Advisory-by-filesystem** — no `fcntl`/`flock`, no OS-level ownership.
   Pure file-presence check.
3. **Leaks on crash** — a process that dies mid-transaction leaves the lock
   behind as a zombie with no self-repair.
4. **No owner identification** — the file is empty. No PID, hostname,
   operation, or reason is recorded anywhere.
5. **No queuing** — hitting the lock returns an error. There is no wait /
   block / retry built into git itself.
6. **Size-0 is NORMAL** — git creates the lock file with 0 bytes as the
   first step of acquisition, then writes to it. Any staleness heuristic
   that treats size-0 as "dead" has a race window that can corrupt a
   legitimately held lock.

For a multi-agent system like GRIP — where multiple Claude Code sessions,
hooks, and automation may touch the same repository — this substrate is
brittle. Point 6 specifically was a latent bug in `lib/safe-git.sh`, which
auto-deleted any 0-byte lock it found.

## The GRIP Layer Above Git

The solution is not to replace git's lock — that is not ours to touch —
but to **serialise GRIP callers before they reach git**. The library
`lib/git_lock.py` provides a context-manager mutex that sits above git and
ensures only one GRIP consumer is executing a git transaction at a time.

```
  ┌─────────────────────────────────────────────────────────────┐
  │  GRIP callers: closeout.py, grip_save.py, hooks, /shipit,   │
  │  RSI sprints, saving-state, ...                             │
  └─────────────────────────┬───────────────────────────────────┘
                            │
                            ▼
             ┌──────────────────────────────┐
             │  lib/git_lock.py (NEW)       │
             │  fcntl.flock on              │
             │  ${git-common-dir}/grip-lock │
             │                              │
             │  - blocking acquire          │
             │  - timeout queue             │
             │  - metadata JSON sidecar     │
             │  - auto-release on crash     │
             │  - status CLI                │
             └──────────────┬───────────────┘
                            │
                            ▼
             ┌──────────────────────────────┐
             │  git subprocess              │
             │  .git/index.lock             │
             │  (git's own lock, unchanged) │
             └──────────────────────────────┘
```

With only one GRIP caller ever reaching git at a time, `.git/index.lock`
collisions between GRIP sessions become structurally impossible. External
callers (human terminal, IDE, launchd hooks) are unaffected — those are
handled by the secondary retry layer in `lib/safe-git.sh`.

## Key Properties

| Property | Git's native lock | GRIP git lock |
|----------|------------------|----------------|
| OS-level ownership | No (file presence) | **Yes** (`fcntl.flock`) |
| Auto-release on crash | No (leaks as zombie) | **Yes** (kernel closes fds on death) |
| Queue on contention | No (hard fail) | **Yes** (blocking acquire with timeout) |
| Owner metadata | Empty file | **JSON** sidecar: pid, hostname, operation, reason, acquired_at, age |
| Worktree correctness | Per-worktree lock | **Shared** via `--git-common-dir` |
| Per-repo granularity | Yes | **Yes** (inherited) |
| Status introspection | None | **CLI**: `python3 -m lib.git_lock status` |

## File Layout

The lock lives inside the git common directory so it never leaks into
tracked content:

```
${repo}/.git/grip-lock        # fcntl token (empty file; flock target)
${repo}/.git/grip-lock.json   # metadata sidecar (human-readable holder info)
```

**Why `--git-common-dir` and not `--git-dir`?** Linked worktrees have
different `--git-dir` paths (`.git/worktrees/<name>/`) but share the same
`--git-common-dir` (the main `.git/`). Anchoring the lock on the common
dir ensures a main-tree session and a worktree session acquire the SAME
file and therefore serialise correctly. For plain (non-worktree) repos
the two return identical paths, so this is strictly safer with no
downside. This fix landed during the W5 red-team pass of the sprint
that introduced the library — a case study in why falsification phases
matter.

## Usage

### Python (primary consumer surface)

```python
from lib.git_lock import grip_git_lock, GitLockTimeout, current_holder

# As a context manager — blocks up to `timeout` seconds before raising.
with grip_git_lock(operation="commit", reason="closeout ritual", timeout=30):
    subprocess.run(["git", "add", "-u"])
    subprocess.run(["git", "commit", "-m", "..."])

# Non-blocking introspection.
holder = current_holder()
if holder is not None:
    print(f"locked by pid={holder.pid} op={holder.operation!r} age={holder.age_seconds:.1f}s")
```

### Shell (via status CLI)

```bash
# Inspect
python3 -m lib.git_lock status
# => {"held": false, "holder": null}

# Test/debug: hold the lock for N seconds
python3 -m lib.git_lock hold 5
```

### Graceful degradation

Callers can wrap `grip_git_lock` in a try/except on `GitLockTimeout` to
record the holder and skip the stage rather than crashing. The `closeout.py`
`_guarded_git` helper demonstrates the pattern:

```python
@contextmanager
def _guarded_git(operation, reason, result):
    try:
        with grip_git_lock(operation=operation, reason=reason, timeout=45.0):
            yield True
        return
    except GitLockTimeout as e:
        holder = e.holder
        result.errors.append(
            f"{operation}: held by pid={holder.pid} age={holder.age_seconds:.1f}s"
            if holder else f"{operation}: GRIP git lock timeout"
        )
    yield False
```

The closeout never crashes on contention — it records why and moves on.

## Consumer Wiring Status

| Consumer | File | Status |
|----------|------|--------|
| closeout commit | `lib/closeout.py` `_closeout_commit` | **Wired** |
| closeout push | `lib/closeout.py` `_closeout_push` | **Wired** |
| safe-git shell wrapper | `lib/safe-git.sh` | **Fixed** (0-byte auto-delete bug removed; stale-lock detection now requires both age-threshold AND lsof confirmation) |
| grip_save / saving-state | `lib/grip_save.py` | Not needed — touches no git |
| shipit | `skills/shipit/*` | TODO (future sprint) |
| auto-ship | `skills/auto-ship/*` | TODO (future sprint) |
| RSI sprint commits | `lib/rsi/*.py` | TODO (future sprint) |

## Safety Fix in safe-git.sh

`lib/safe-git.sh` previously auto-removed any `.git/index.lock` it found at
size 0 OR older than 60 seconds. The size-0 rule was a race bug: git
creates the lock at size 0 as its first acquisition step. A concurrent
`safe_git` call could delete a legitimately held lock during that race
window.

The new staleness criteria require **both** of:

1. File age > `SAFE_GIT_STALE_LOCK_AGE` seconds (default 60)
2. No live process has the file open (checked via `lsof -t`)

If `lsof` is unavailable or returns any PID, the lock is left alone. For
truly stuck locks an operator should run `python3 -m lib.git_lock status`
first to confirm no GRIP consumer is legitimately holding the GRIP mutex,
then decide by hand whether to remove `.git/index.lock`.

## Red-Team Findings (W5)

Attack vectors considered during the falsification phase:

| Attack | Verdict | Action |
|--------|---------|--------|
| fcntl on NFS / network FS | Real risk for remote repos; silently degrades to no-op on some old kernels | Documented limitation; `~/.claude` is local APFS, not affected |
| Worktree uses separate git-dir → independent locks | **Real bug** | **FIXED**: switched to `--git-common-dir`. Regression test `test_main_tree_and_worktree_share_lock` asserts main + worktree share one lock |
| macOS fcntl per-fd quirks | Not a problem in practice | Proven by `test_second_process_queues_and_acquires` |
| GitLockTimeout metadata can be stale | Cosmetic only | Accepted |
| External `git` callers bypass the GRIP lock | True and by design | Secondary defence is `safe-git.sh` retry layer |
| Clock drift in `acquired_at` | Cosmetic only | Accepted |
| `.git/grip-lock` leaking into tracked content | Impossible — git ignores its own dir | No risk |

## Failure Modes Still Uncovered

1. **External git callers**: a human running `git commit` in iTerm is not
   part of this serialisation and can still collide with GRIP writes. The
   safe-git.sh retry layer absorbs transient collisions; a persistent human
   hold will still fail a GRIP caller. This is acceptable — humans should
   win in that race.
2. **NFS-mounted repos**: `fcntl.flock` semantics on NFS are kernel-dependent.
   This architecture assumes local filesystem.
3. **Cross-host coordination**: if the same repo is accessed over SMB/AFP
   from multiple machines, the lock is not distributed. Out of scope.

## Test Coverage

`tests/test_git_lock.py` covers:

- Clean acquire/release leaves no artefacts
- Cross-process queuing (child waits ≥0.8s for parent release)
- Timeout raises `GitLockTimeout` carrying holder metadata
- `SIGKILL` of holder releases immediately (the property git's lock lacks)
- Status CLI returns valid JSON in both held and free states
- Non-git directories raise `GitLockError`
- Separate repos do not block each other (granularity)
- **Main tree and linked worktree share the same lock** (red-team fix)

Run: `PYTHONPATH=~/.claude pytest tests/test_git_lock.py -v`

## Falsification Criterion for H022

> H022: A GRIP-layer fcntl.flock advisory git mutex reduces concurrent-session
> git collisions from hard-fail to soft-queue and restores closeout
> completability when 2+ Claude sessions are active on the same repo.

**Falsified if**:
- Closeout still hard-fails on `.git/index.lock` collision under contention
- The new mutex introduces its own stale-lock pathology
- Multi-session tests show wait times > 30s at the P95
- Red-team findings reveal exploitable race windows beyond those listed above

Verification protocol: re-run closeout under a deliberate 2-session
contention scenario and confirm completion. Deadline 2026-04-18.

## Future Work

- Wire `shipit`, `auto-ship`, and RSI sprint commit/push paths through
  `_guarded_git` in the same way closeout was wired
- Add a worktree-escalation hook that detects concurrent sessions and
  mechanically pushes new work into isolated worktrees (part of Rule 13
  enforcement, deferred from this sprint's W4)
- Consider promoting `grip_git_lock` to a PreToolUse hook on all Bash
  `git add/commit/push` invocations for fully mechanical enforcement
