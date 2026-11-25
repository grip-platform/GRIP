# Task Lease — Claim Before Work on the Shared Pool (PARAMOUNT)

Origin: 2026-07-13. V>> typed `/back` in every live session after an AFK window;
each session then independently picked "the next valuable safe work" from the
SAME shared pool and started the SAME task (V>>: *"I thought that each session's
`/back` work that should be resumed is locked per session — with that context.
oops."*). The class was already live before that night: #3944 duplicated
already-merged #3943 (2026-07-12), and the #3970/#3971 DEFER sweep was
double-picked — the sibling's PR #3981 merged 03:20Z, and PR #3982 had to be
reconciled down to "keep only the complement" (commit `62514f32`). The same
night a third start (of already-merged #3993) was prevented only by the
check-origin-main-first memory. N≥3 → rule now, per
`knowledge-maturation-functor.md`.

## The failure shape

N concurrent sessions each read the SAME shared work pool (harness task ledger,
open AGORA DEFER follow-ups, open PRs) and each independently compute "the next
valuable safe item" with the same selection bias → the same item, started N
times, on N *different branch names* — so the branch locks
(`state/branch-locks/`, `lib/grip-worktree.sh`) never fire. The duplication tax
lands later: a duplicate PR (#3944), a reconcile-to-complement pass (#3982), or
reviewer/CI cycles spent on dead work.

Per-session conversation context is NOT the problem — each session resumes its
OWN thread correctly. The collision is at **shared-pool pickup**.

## The Law

> Before starting ANY item from a shared pool — a harness task, an unowned
> AGORA DEFER follow-up, an unclaimed PR ride — a session MUST claim it and
> VERIFY the claim landed, and MUST skip items carrying a live claim.

## The protocol (existing primitives only — no new machinery)

1. **CLAIM** — `TaskUpdate(taskId, owner=<session-id>, status=in_progress,
   metadata={leased_at: <ISO-8601 UTC>})`. `<session-id>` is the harness
   session id (`$CLAUDE_SESSION_ID`); fall back to the worktree/branch name.
   A DEFER issue or PR ride with no harness task gets one first
   (`TaskCreate`) — the ledger IS the lock surface.
2. **VERIFY** — `TaskGet(taskId)`: proceed ONLY if `owner` is you. The
   read-back approximates compare-and-set on a store we do not host; the
   ledger is the one shared surface every session already loads.
3. **SKIP live claims** — `owner` set + `in_progress` + `leased_at` fresher
   than the TTL means another session owns it: take the NEXT item, never race.
4. **Lease TTL** — a lease is an open record and must expire
   (`lifecycle-closed-or-expired.md`): `leased_at` older than **4h** ⇒ stale
   (the claimant may be dead) — re-claim via steps 1–2 and note the takeover
   in the task. No reaper process: staleness is evaluated read-side at pickup,
   like `next_up`'s mtime TTL.
5. **RELEASE on abandon** — stopping without finishing ⇒
   `TaskUpdate(status=pending, owner="")` so the pool stays honest
   (status is telemetry; an abandoned "in_progress" is a stale frame).

## Mechanical enforcement (L2 — `lib/task_claims.py`, #436/#2795)

L1 above claims on the harness `owner` field and read-back-verifies. That has a
TOCTOU window: two same-host sessions can both `TaskUpdate(owner=…)` before
either `TaskGet`s, and the field is last-writer-wins. `lib/task_claims.py`
closes that window for the SAME-HOST case — where every observed collision
happened — with an atomic host-local lease:

```
python3 lib/task_claims.py claim   <task_id> $CLAUDE_CODE_SESSION_ID   # exit 0 = yours; 1 = a live session holds it
python3 lib/task_claims.py release <task_id> $CLAUDE_CODE_SESSION_ID   # on done / abandon
```

- **Atomic** — the claim is an atomic read-modify-write under a `FileLock` on its
  own registry (`cache/task-claims.json`) — true mutual exclusion, not the racy
  read-back of L1 — so of N same-host sessions racing one task exactly one gets
  exit 0.
- **TTL 2h + liveness** — a claim expires after 2h (`DEFAULT_TTL_HOURS`) OR
  earlier if the owning session is provably dead (its transcript
  `~/.claude/projects/*/<sid>.jsonl` has been idle past the window). Liveness is
  fail-safe toward held: an indeterminate probe NEVER frees a live claim.
- **Order** — call L2 `claim` FIRST at pickup; on exit 0 proceed and also record
  L1 (`owner`, `in_progress`) for cross-surface visibility; on exit 1 skip to the
  next unclaimed item. L2 is the mechanical gate; L1 is the shared-ledger signal
  other surfaces (and other machines) still read.
- **Session id** — pass `$CLAUDE_CODE_SESSION_ID` (the `<sid>` in the transcript
  path); it must match the `.jsonl` filename for the liveness probe to resolve.

## What this is NOT

- **The harness owner field cannot be flocked; a host-local companion registry
  can.** L1 (steps 1–5) is the behavioural claim on the harness `owner` field —
  read-back-verify, the strongest CAS on a store we do not host. L2
  (`lib/task_claims.py`, §above) is the mechanical complement: a host-local lease
  over its OWN `FileLock`-guarded registry, so two SAME-HOST sessions racing one
  task cannot both win. L2 landed (#436/#2795) because the class reached N≥3 with
  only L1 available (`knowledge-maturation-functor.md`: rule-now → shim-at-N=3).
  L3 — a mesh-backed CROSS-MACHINE lease (grip-session-mesh hosts real cross-host
  shared state) — remains deferred until a cross-host collision is observed
  (every collision so far was same-host).
- **NOT for the four HALT classes or V>>-gated items** — those are never
  autonomous work regardless of lease state.
- **NOT a licence to hoard** — claim ONE item at a time; a claim without
  progress is released or goes stale.

## Falsification

Wrong if, over the next ≥10 multi-session windows: duplicate same-task starts
still occur WITH L1+L2 both followed (host-local `FileLock` CAS also too weak →
escalate to the L3 mesh lease); OR the TTL (L1 4h / L2 2h) causes takeovers of
live sessions more often than it frees dead leases (mis-tuned — lengthen, don't
drop); OR claiming overhead
measurably slows single-session work (then scope enforcement to
multi-session contexts, detected via the `pgrep -x claude` count). Track:
duplicate-task PRs / reconcile-to-complement passes per month (target 0;
2026-07-12/13 had ≥2 confirmed + 1 near-miss).

## Scope

Every autonomous or semi-autonomous pickup from a shared pool: `/afk` loops,
`/auto` `/rsi` waves, AFK ticks, relay-spawned sessions, and interactive
"pick up #N" turns. Enforced by `lib/task_claims.py` (L2, tests
`tests/test_task_claims.py`). Sits beside `lifecycle-closed-or-expired.md` (the
lease TTL — mirrored by L2's own TTL + liveness reaper), `agent-dispatch-protocol.md`
L4 (verified evidence pre-dispatch), `verify-canonical-not-claimed.md` (check
origin/main before building — the read-side complement that catches what claiming
misses), and the branch locks in `lib/grip-worktree.sh` (branch-level complement).
Mirror into `~/.hal/rules/` if HAL sessions gain a shared pool.
