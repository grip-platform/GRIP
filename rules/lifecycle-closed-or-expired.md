# Lifecycle: Closed-or-Expired, Never Silently-Accreting (PARAMOUNT)

Origin: 2026-06-02. V>> flagged grip-session-mesh reporting "1342 message(s) are
waiting" when ~1333 were addressed to sessions that had exited days ago ("this is
not right ... no STALE work as framing downstream"). The RCA (PR #3381) found the
bug was an *instance of a class*: a record born with an open flag, no guaranteed
closer, no TTL, and a report that counts the whole open set as "current work". A
/ultrathink ("is this pattern present elsewhere in GRIP/HAL?") found the same
shape across several subsystems. This rule names the class so the next instance is
caught at design time, not after it reaches an operator as a stale frame.

## The failure shape — "unclosed-lifecycle accretion"

A lifecycle record is born OPEN (a pending flag, an undelivered row, a held lock,
a registered session, an in-flight hypothesis) under **all four** of:

1. **create-with-open-flag** — something sets `open / pending / delivered=0 / locked`.
2. **no guaranteed closer** — the closing transition depends on a liveness that can
   vanish (the recipient never polls; the holder crashes; the deadline passes
   unobserved). Nothing *guarantees* the close fires.
3. **no TTL / GC** — an unclosed record lives forever; nothing reaps it.
4. **report raw-open-count as current** — a status surface counts the entire open
   set as live/current work, conflating *dead-but-open* with *alive-and-pending*.

The four together accrete dead records and then **frame them as current** — that
is the stale-framing. Any one conjunct broken in isolation is benign; the
conjunction is the bug.

## The mandate (design-time gate for any open→closed record)

When you introduce or touch a record with an open→closed lifecycle, it MUST have:

- **A guaranteed closer OR a TTL.** Either the close transition is guaranteed
  (idempotent, retried, fail-closed), or an unclosed record expires on a TTL and is
  reaped by a GC. Liveness-dependent closing with no TTL backstop is the defect.
- **Live-vs-expired separated in every report.** A status surface counts only the
  *live* set as current; expired/dead-but-open records are a SEPARATE,
  clearly-labelled "pending cleanup, not current work" line — never folded into the
  live count. This is the load-bearing half: even a backlog that never drains must
  never be *framed* as current.
- **A fail-closed reaper, if reaping.** The GC deletes only records that are BOTH
  expired AND safe to drop (e.g. old AND recipient-dead) — never one a live-but-idle
  actor could still legitimately close. Bounded memory, chunked, and atomic with its
  liveness re-check (no select-then-delete race).
- **An autonomous trigger for the reaper.** A reaper that only runs on a MANUAL
  command is no reaper — the TTL goes unenforced between invocations and the backlog
  accretes anyway. Wire the reap into something that fires WITHOUT operator action
  (a per-session heartbeat hook, a cron/launchd, a self-throttled opportunistic call
  on a hot path), self-throttled so N callers do ~one real reap per interval. A
  TTL + a fail-closed reaper are necessary but NOT sufficient: the reaper must
  actually FIRE on its own. (mesh_gc had a correct reaper from PR #3381 but no
  trigger — it ran only on a manual `bin/mesh state`, so 2315 inbox + 276 session +
  218 snapshot tombstones accreted anyway until PR #3573 wired `maybe_reap` into the
  5-min live-dump hook.)

## Instances (the N this rule generalises over)

| Subsystem | Open record | Status (2026-06-02) |
|-----------|-------------|---------------------|
| grip-session-mesh inbox | `delivered=0` rows to dead recipients | **FIXED — instance #1** (`lib/mesh_gc.py`, PR #3381 built the reaper; **PR #3573 gave it an autonomous trigger**): honest live-count + dead-tombstone GC + "pending cleanup" line, now self-firing via the 5-min live-dump hook. #3381's reaper was correct but trigger-less (manual-only); the 2026-06-18 2315/276/218 backlog proved a TTL+reaper without an autonomous trigger still accretes |
| grip-session-mesh sessions / `cc-*-LIVE.md` | session rows + snapshot files of exited sessions | FIXED with #1 (TTL_SESSION reap + live-filtered snapshot list) |
| `next_up.md` serializer fallback | manual md file, no closer — a March Gen-423 fossil fed every auto-checkpoint instance + signed context-chain node for 3 months, re-stamped fresh each serialization | **FIXED — instance #2** (2026-06-10, PR #3508): 7-day mtime TTL in `instance_serializer._find_next_up_md` (read-side expiry; no reaper — nothing to archive); fossil deleted. N=2 on the ttl_reaper ladder — TTL idea copy-adapted, mesh_gc's reaper shape NOT duplicated; generic shim deferred while the threshold was N=3, eligible at N=2 since 2026-10-03 |
| harness tasks | `pending` tasks long since done/abandoned (40+ stale) | OPEN — `/task-reconcile` (`lib/task_reality_reconcile.py`) is a read-side mitigation; no closer/TTL on the write side |
| git index locks | stale `index.lock` from crashed/slow tool calls | OPEN — manual clear; no TTL reaper |
| hypothesis engine | hypotheses past deadline, never verified | SUSPECTED — verify-at-deadline exists; unverified-past-deadline accretion unmeasured |
| telemetry JSONL | append-only growth (dispatch-failures, gate-metrics, …) | BY-DESIGN append; benign *iff* never counted as "current" |

## Enforcement tier (knowledge-maturation-functor)

**Rule-at-N=1** because severe: the first instance reached V>>'s attention as a
trust-eroding stale-frame on a live substrate. Review-and-habit enforced now.

The generic **shim is DEFERRED** (`lib/ttl_reaper.py` — a reusable
expire-and-reap with the live-vs-expired report split baked in) until **N=3
independent reapers** exist. `lib/mesh_gc.py` is instance #1's concrete reaper;
abstracting a generic reaper now would be premature (YAGNI / YSH-inverse). Path:
rule now → a 2nd lifecycle reaper copy-adapts mesh_gc's shape and notes the
duplication → the 3rd extracts the shim with the *report-split invariant* (the
live count provably excludes expired records) enforced **in the shim itself**, not
per-callsite.

## Falsification

Wrong if, over the next ≥3 lifecycle records it governs: every one already had a
guaranteed closer or TTL (the gate addresses a non-problem), OR enforcing the
report-split measurably confused operators more than the raw count did, OR the
four-conjunct decomposition fails to classify a real stale-framing incident (the
shape is wrong). Track: stale-framing incidents reaching an operator per quarter
(target 0; was ≥1 on 2026-06-02 — the mesh "1342 waiting").

## Scope

Every GRIP/HAL subsystem with an open→closed record lifecycle: message queues,
session/presence registries, locks, task ledgers, hypothesis/deadline trackers,
any "pending/in-flight" set a status surface counts. Sits beside
`verify-canonical-not-claimed.md` (status is telemetry, not truth) and
`knowledge-maturation-functor.md` (rule@N=1 → shim@N=2). Mirror into `~/.hal/rules/`
when a HAL lifecycle instance appears.
