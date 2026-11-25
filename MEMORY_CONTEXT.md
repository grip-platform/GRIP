# MEMORY_CONTEXT.md — the Context Memory-Chain

> *"An IDR records the decision. The context chain records the mind that made it."* — V>>--<<V
>
> A signed, replayable chain of what GRIP *believed* at each checkpoint. Sibling
> specs: [`IDR.md`](IDR.md) (the decision record it pairs with),
> [`HAPPI.md`](https://github.com/grip-platform/HAL/blob/main/happi.md) (the protocol — `~/.hal/happi.md`,
> `context` event type), doctrine [`rules/cryptographic-causation.md`](rules/cryptographic-causation.md).

**Status**: live in production. Reference implementation:
[`lib/context_chain.py`](lib/context_chain.py) (write + verify) +
[`lib/context_head.py`](lib/context_head.py) (HEAD pointer) +
[`lib/context_snapshot.py`](lib/context_snapshot.py) (fold + snapshot) +
[`lib/context_gc.py`](lib/context_gc.py) (reaper + ledger-fold) +
[`lib/context_warmstart.py`](lib/context_warmstart.py) (read-side warm-start). Signing
is shared with the IDR ([`lib/precog/idr.py`](lib/precog/idr.py)); verification with
the forest ([`lib/idr_forest.py`](lib/idr_forest.py)).

---

## WHAT — the one-paragraph version (plain English)

The **context memory-chain** is an append-only, signed history of GRIP's evolving
mental model. Every time GRIP checkpoints (a `/save`, an end-of-request, an
explicit checkpoint), it writes one signed **`context-delta`** node capturing the
*belief at that moment*: the next step it intends to take, a short summary of what
it now knows, and how important that knowledge is. Each node links its predecessor
(same machinery as an [IDR](IDR.md)), so the deltas form a chain rooted at an
exogenous anchor. Periodically the chain is **folded** into a bounded memory
kernel and pinned by a **`context-snapshot`** node. Because every node is
HMAC-signed, **the recorded belief at any past checkpoint is tamper-evident** —
re-run the verifier for `VERIFIED` / `BROKEN`. The chain is the durable,
auditable memory substrate a fresh session warm-starts from.

ELI5: it's a diary where every page is sealed with the same wax stamp as the
decision receipts. You can flip back to any page and prove nobody edited it after
it was written — so tomorrow's GRIP can trust yesterday's notes.

---

## WHY — the load-bearing reason

A decision record ([IDR](IDR.md)) tells you *what* was decided. But "why did GRIP
do that?" needs the **belief state** at decision time — the next-step intent, the
working summary, the importance it assigned. Storing that as an unsigned scratch
file (the old `next_up.md` / instance-index path) means it can rot or be edited
with no trace, and a fresh session that warm-starts from it is trusting unsigned
state. The signed context-chain fixes both: warm-start reads from the **latest
VERIFIED node**, so a tampered or corrupted head is *skipped loudly*, never
silently trusted. It is the same provability moat as the IDR, applied to **memory
and continuity** instead of a single decision.

This also closes a real recurring bug class: an unsigned, unbounded memory store
(`HOLISTIC.md` was at 39,800/40,000 chars) silently truncates and silently
accretes. The chain's fold + snapshot + reaper keep the *live* kernel bounded
while every collapsed fact stays recoverable — see
[`rules/lifecycle-closed-or-expired.md`](rules/lifecycle-closed-or-expired.md).

---

## HOW — the chain format (verify-canonical against `lib/context_chain.py`)

A context node is a `PrecogIDR` (the **same dataclass** as an [IDR](IDR.md) — see
[`IDR.md`](IDR.md) for the full field table and the `audit` signature block). The
context layer specialises it by `kind` and by what the `decision` body carries.

### `context-delta` — one belief checkpoint

Written by `checkpoint(...)` → `append_context(...)`. The `decision` body
captures the **agent's mental model at that checkpoint**:

| `decision` field | Type | Meaning |
|------------------|------|---------|
| `next_step` | object | The volatile ~1 KB mental-model "what I intend to do next", stored **by value** (this is what warm-start reads). `{}` when there was no next step. |
| `summary` | string | A short (≤600 char) kernel summary of the achievement / belief. |
| `title` | string | Human label for the checkpoint. |
| `tier` | string | Fold priority — `feedback` > `project` > `user` > `reference`. Classified by [`lib/context_chain_classify.py`](lib/context_chain_classify.py). |
| `paramount` | bool | `true` if the checkpoint references a never-drop (PARAMOUNT) fact — enters the snapshot's completeness commitment. |

Chain mechanics (identical to the IDR's): `predecessor_idr` links the prior node;
the **genesis** delta (no prior HEAD) links the exogenous anchor
`council:context-chain-genesis` so the chain roots into a verifiable
[Forest](IDR.md); `depth` is the distance from that root. Append is
**build → append (flock + fsync) → atomic HEAD swap**.

### `context-snapshot` — a pinned fold

Written by `snapshot_chain(...)`. It folds the live chain into the bounded kernel
via the existing deterministic reducer (no second fold), then records the fold so
it is replayable. Its `decision` body carries:

| `decision` field | Meaning |
|------------------|---------|
| `kernel_sha256` | `payload_digest` of the folded kernel — re-folding the same chain reproduces it (idempotency anchor). |
| `substrate_digest` | The fold's input digest (timestamp-free, so the fold replays byte-identically). |
| `folded_nodes` | How many deltas the snapshot subsumes. |
| `completeness` | A **shadow** RFC-6962 Merkle root over the content-addresses of the chain's PARAMOUNT deltas + a cardinality `paramount_n`, so a single never-drop fact's membership is provable with an `O(log N)` inclusion proof (`paramount_inclusion_proof`) — and a dropped paramount fact becomes a detectable root divergence at re-fold, not a silent truncation. "Shadow" = recorded + signed but not yet the live completeness authority. |

### `context-supersede` — a forward-only correction

The chain is append-only, so a correction is a new node that the fold *masks* the
old one with — history is never rewritten, only superseded forward.

Written by `supersede_context(target_id, correction, reason=…)`
(`lib/context_chain.py`). Its `decision` body is the `correction` — any fields a
delta may carry (`title`, `summary`, `next_step`, `tier`, `paramount`, …) — plus
three link fields the caller may not set:

| `decision` field | Meaning |
|------------------|---------|
| `supersedes` | The `id` of the node being corrected. It must be in the live chain and must not be a `context-snapshot` (a fold pin is not a memory claim; correcting a fold is a re-fold). Anything else is refused before any I/O. |
| `supersedes_addr` | The target's content address, so the correction binds the exact bytes it replaces, not just a name. |
| `reason` | Why — required, non-empty, signed with the rest. |

What masks and what does not — the line is **view** versus **history**:

- **Masked** (`mask_superseded`): the fold (`chain_to_entries`, so the kernel
  carries the correction and not the delta it replaced) and warm-start
  (`resolve_next_up` never returns a superseded checkpoint's `next_step`). A
  supersede that is itself superseded is masked too, and its target stays masked.
- **Not masked**: `verify_context_chain` (every node keeps its signature and nothing
  is rewritten, so the chain stays `VERIFIED`) and the paramount completeness
  commitment (`paramount_root` / `paramount_n` still count a superseded paramount
  fact — the root answers whether a fact was *ever* recorded, and dropping it
  would read as the silent truncation the commitment exists to detect).

### The HEAD pointer

`state/precog/context-HEAD.txt` holds the id of the latest node. Reads/writes are
atomic (write-temp + `os.replace`), and the path is **per-session namespaced**
(`context-HEAD-<4hex>.txt`) so two concurrent `~/.claude` sessions keep
independent chains. `read_latest()` is the single shim every reader routes through.

---

## WHEN — and how it PAIRS with an IDR

The two chains are **independent but cross-linked**. A [decision IDR](IDR.md)
lives in `state/precog/idr.jsonl`; a context delta lives in
`state/precog/context.jsonl`. A context delta records *the belief that produced a
decision* by carrying that decision-IDR's **content address** in
`decision.records_idr` (the `append_context(records_idr=...)` cross-reference):

- `predecessor_idr` is **always** the same-file temporal spine (root→leaf). It is
  never reused for the cross-reference.
- `records_idr` is a **cross-chain pointer**: the `content_addr` of the signed
  decision-IDR this context delta records. It is folded **into the signed body
  before signing**, so a tampered `records_idr` byte makes the context chain
  `BROKEN`.
- `resolve_citation(delta)` is the read-side probe: `RESOLVED` if the cited address
  is present in the decision-IDR ledger, `DANGLING` if absent, `RESOLVED` (vacuous)
  if uncited. It is **fail-open and signature-independent** — a `DANGLING` citation
  does **not** make the chain `BROKEN` (the signed link is `predecessor_idr`; the
  citation is a separate axis).

So: the IDR answers *"what was decided, provably?"*; the paired context delta
answers *"what did GRIP believe when it decided, provably?"* — and the cross-link
ties the belief to the decision without conflating the two chains.

In HAPPI (`~/.hal/happi.md`) this is the **`context` event** — the structural twin
of the `idr` event, a non-streaming terminator carrying a `context_ref` whose
`sha256` is byte-identical to GRIP's `content_addr` for the same body.

---

## Verify · replay · warm-start · GC

- **Verify** — `verify_context_chain()` rebuilds the forest and returns the
  tri-state `Verdict`, requiring **both** axes for `VERIFIED`: (a) **spine HMAC** —
  every node's signature matches its body (a flipped delta byte → `BROKEN`); and
  (b) **blob-presence** — a snapshot that references a kernel blob
  (`decision.kernel_blob_addr`) is `BROKEN` unless that blob exists on disk *and*
  its bytes hash back to the recorded address (a SHA-256 is tamper-*evidence*, not
  tamper-*recovery*; axis (b) adds the existence proof). Both axes are
  monotone-toward-safe: a node written before an axis shipped is never retroactively
  broken; an empty chain is vacuously `VERIFIED`.
- **Replay** — `replay_context()` deterministically re-derives the chain root→HEAD
  (pure, provider-agnostic, no LLM), returning the reconstructed sequence, the
  verdict, and a stable `replay_digest`.
- **Warm-start** — `resolve_next_up()` returns the `decision.next_step` of the
  **newest VERIFIED** node carrying a non-empty next-step. If a `BROKEN` head forces
  falling back to an older verified node, it emits one **loud** stderr line and
  never fabricates a next-step. An absent/empty/unverifiable chain returns `None` so
  the caller falls through to the legacy read — merging the chain reader changes
  nothing until a chain actually exists (the safety contract).
- **GC** (`lib/context_gc.py`) — two halves keyed on a *verified* snapshot:
  (1) a **reaper** archives the delta prefix the snapshot already subsumes to a cold
  archive (recoverable by content address — **never hard-deleted**), reporting `live`
  vs `archived_collapsed` separately so collapsed history is never framed as current
  work; (2) a **ledger-fold** collapses provably-subsumed ledger entries into one
  counted, recoverable pointer line so the live kernel shrinks while staying
  complete. Both fail-closed: no verified snapshot ⇒ reap nothing.

---

## A real node (verbatim shape from `lib/context_chain.py`)

This is the actual JSONL line `append_context` writes for a `checkpoint(...)` call
that records *"authored the spec files; next, open the PR"* (keys canonically
sorted; this minimal-tier node carries `decision_anatomy` as `null`, excluded from
the content address):

```json
{"audit":{"key_fingerprint":"9eaa61e2","scheme":"hmac-sha256","signature":"hmac-sha256:f74462a0afc5a050ddf19941a258679c127584bb76af3e6183b1c7b98e648559"},"decision":{"next_step":{"branch":"feat/idr-memory-context-specs","next":"open the PR"},"paramount":false,"summary":"Authored IDR.md + MEMORY_CONTEXT.md polyglot specs","tier":"feedback","title":"spec authoring checkpoint"},"decision_anatomy":null,"depth":0,"fingerprint":"956fdc0db578ba37dcf0c8dc9b2e37055e2c053ab96b8aaac42f143be2206bb8","happi":"1.1","id":"precog-1782329908-7f61d520","inputs":{},"kind":"context-delta","predecessor_idr":"council:context-chain-genesis","ts":"2026-06-24T19:38:28Z"}
```

For that node: `context_addr` =
`sha256:274a1c87a7d58fd96467a46bfb7cb397855cdca8beca5f851ff07bfe2540a213`,
`verify_context_chain` = `VERIFIED`, and `replay_context` reproduces it with a
stable digest. The test [`tests/test_idr_memory_specs.py`](tests/test_idr_memory_specs.py)
parses this fenced example and asserts it round-trips and verifies against the real
`lib/precog/idr.py` — so if this spec drifts from the code, the test goes red.

---

## Falsification (this spec is wrong if…)

- a context node whose any signed field is altered without re-signing ever verifies
  `VERIFIED`;
- warm-start ever returns a next-step from an **unverified** node, or fabricates one
  when no verified node carries it;
- a reaped delta is not recoverable by content address from the cold archive (the
  "never hard-delete" invariant);
- `replay_context` produces a different `replay_digest` on a second run of the same
  chain;
- the fenced example above fails to round-trip against `lib/precog/idr.py` (spec
  drifted from code).

---

*Cross-references: [`IDR.md`](IDR.md) ·
[`HAPPI.md`](https://github.com/grip-platform/HAL/blob/main/happi.md) ·
[`rules/cryptographic-causation.md`](rules/cryptographic-causation.md) ·
[`rules/lifecycle-closed-or-expired.md`](rules/lifecycle-closed-or-expired.md).
Canonical implementation: `lib/context_chain.py` (+ `context_head` /
`context_snapshot` / `context_gc` / `context_warmstart` / `context_chain_classify`).
Watermark: V>>--<<V*
