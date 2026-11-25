---
name: holistic-memory
description: Synthesised, session-inherited memory kernel. Reduces ~860 fragmented memory files + the truncating MEMORY.md index into one bounded HOLISTIC.md a fresh session inherits at zero in-session token cost, with an R1 completeness ledger that guarantees no PARAMOUNT/feedback rule is ever silently dropped. Cross-session propagation via digest-bearing mesh broadcast.
invocable_name: holistic
triggers:
  - /holistic
aliases:
  - holistic
  - holistic-memory
see_also:
  - recall
  - syncing-sessions
  - kono-memory
  - mesh
---

# /holistic — Synthesised Memory Kernel

The problem this solves, in plain language: GRIP's memory lives in ~860
small files plus a `MEMORY.md` index that is **704 lines long but only the
first ~200 are loaded** every session — the rest is silently truncated. A
new session therefore inherits an incomplete, lossy picture and the
operator does not know what was dropped.

`/holistic` reduces the whole substrate into one bounded `rules/HOLISTIC.md`
kernel that every new session inherits automatically (zero in-session token
cost — it is pre-written to disk and surfaced by `kono_preload`). The
kernel carries a **completeness ledger**: every PARAMOUNT and feedback
memory is listed verbatim with its `/recall` key, even when the body is
compressed away — so a load-bearing rule is never silently lost, only
explicitly deferred to deep recall.

## Subcommands

| Command | What it does |
|---|---|
| `/holistic` or `/holistic update` | Regenerate the kernel from the current substrate: compare-and-skip (no-op if nothing changed) → atomic write → notify concurrent sessions via a digest-bearing `mesh broadcast`. |
| `/holistic refresh` | Alias of `update`. |
| `/holistic sync` | Alias of `update` — the broadcast IS the cross-session sync; peers act only if the broadcast digest differs from what they loaded (own-echo ignored, no action storm). |
| `/holistic verify` | R1 check: exits non-zero if any PARAMOUNT/feedback memory in the substrate is absent from the kernel's ledger. This is the kill-criterion probe. |
| `/holistic inherit` | Show the head of what a fresh session would inherit (the kernel header + ledger start). |
| `/holistic save [achievement]` | Persist every memory subsystem (`grip_save.save_all`) **then** regenerate the kernel — one armed compose; the W0 provenance gate is active for its whole duration. |
| `/holistic advisory` | Cross-session closeout **advisory only**: writes a per-peer advisory file that the *owning* peer self-consumes on its own tick. Write-only — see the bounded-scope doctrine below. |
| `/holistic evo` | Background fractal-evolution chain (insights → evo → learn) in **one isolated `run_in_background` agent**; non-blocking, no broadcast, never claims determinism (evo/learn are best-effort). |

Two thin surfaces, both path-portable (`CLAUDE_DIR` or `$HOME/.claude`),
both via `bin/holistic`:

- **Synthesiser verbs** (`update`/`refresh`/`sync`/`verify`/`inherit`) →
  the tested `lib/holistic_synthesiser.py` syscall.
- **Composed verbs** (`save`/`advisory`/`evo`) → the
  `lib/holistic_router.py` dispatch table (one `{verb: handler}` dict, no
  if/elif). Each handler lazy-imports and calls the canonical W1/W2/W3
  lib unchanged (Rule 21); an unknown verb is a `ValueError` naming the
  known verbs, never a silent no-op.

## Bounded scope, the NO-close-others doctrine, and the W0 gate

This is the binding-DA invariant — the reason the cross-session design is
safe by construction, not by good behaviour:

- **Bounded verb set.** The router exposes only memory-local verbs. There
  is deliberately **no close-others verb, ever.** `/holistic advisory`
  *writes* a per-peer advisory file; the OWNING session reads its own
  advisory on its own tick and runs its OWN `/closeout`. The deciding and
  acting session is always the owner — one session never mutates another
  session's git/branch/working-tree. This is what makes shared-`.git/HEAD`
  corruption structurally impossible rather than merely avoided.
- **W0 provenance DENY gate (cross-wave backstop).** While any `/holistic`
  verb runs it sets a TTL-bounded provenance marker; the W0 PreToolUse
  hook then DENIES any `git commit|push|checkout|switch|reset|rebase` or
  `closeout` for that duration. It is fail-safe (absent/stale/corrupt
  marker ⇒ INACTIVE ⇒ gate ALLOWS — it never blocks normal work) and is
  **not** yolo-relaxable. Defence in depth: even a future code change that
  tried to add foreign mutation is mechanically stopped.

## How it stays correct (the grounded-council invariants)

- **R1 — completeness ledger (non-negotiable).** The cap compresses bodies;
  it never drops a PARAMOUNT/feedback ledger entry. `holistic verify`
  exits non-zero on any gap. A bounded kernel that silently dropped a
  PARAMOUNT rule would be *worse* than the honest truncation it replaces.
- **R2 — idempotent over substrate.** Same memory files ⇒ byte-identical
  kernel payload; volatile fields (timestamps) are excluded from the
  idempotency anchor.
- **R3 — safe propagation.** Atomic `os.replace` write (readers never see a
  torn file) + compare-and-skip single-effective-writer + best-effort
  digest-bearing broadcast (a failed broadcast never loses the write).
- **R4 — never on the launch path.** Synthesis walks ~860 files; it runs
  out-of-band only (the Stop hook + `/save`), never during launch.
  `kono_preload` only *reads* the pre-baked kernel; missing/stale ⇒
  graceful degrade to prior behaviour, never a crashed launch.
- **R5 — a reducer, not a capturer.** It reduces whatever substrate exists;
  it does not duplicate the insight-capture pipeline.

## Activation

The kernel regenerates automatically once the Stop hook is active. The
hook is registered in the canonical `config/settings.reference.json` Stop
chain; the live `settings.json` is per-machine (gitignored), so on each
machine activation is a one-time re-derive of `settings.json` from the
reference (deliberately a manual step — auto-mutating the global live hook
chain is out of scope for an autonomous loop). Until then, run
`/holistic update` manually (or on `/save`).

## When to use

- After a burst of memory writes, to refresh what future sessions inherit.
- When a concurrent session should learn about new durable memory now
  (`/holistic sync`).
- Any time, to audit completeness: `/holistic verify` (zero gaps = healthy).
